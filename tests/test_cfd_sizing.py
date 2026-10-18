"""Contract-size sizing and migrations; every write uses a disposable journal."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import database as db
from journal_mcp import neo, operations, records
from journal_mcp.neo_schema import TABLES
from utils.position_sizing import IC_MARKETS_CONTRACTS, size_cash_risk

ROOT = Path(__file__).resolve().parents[1]
NOW = '2026-10-18T20:00:00+10:00'


class CashSizingTests(unittest.TestCase):
    def test_fractional_gold_us500_and_costs(self):
        gold = operations.risk_calculator(3000, 2997, 10000, instrument_key='ic_gold')
        self.assertEqual((gold['quantity'], gold['actual_risk']), (0.33, 99))
        index = operations.risk_calculator(6000.25, 5993, 10000, instrument_key='ic_us500')
        self.assertEqual((index['quantity'], index['actual_risk']), (13.7, 99.33))
        costed = operations.risk_calculator(3000, 2997, 10000, instrument_key='ic_gold', costs_per_contract=7)
        self.assertEqual(costed['quantity'], 0.32)
        self.assertEqual(costed['actual_risk'], 98.24)
        short = operations.risk_calculator(6000.25, 6007.50, 10000, direction='SHORT', instrument_key='ic_us500')
        self.assertEqual(short['quantity'], 13.7)

    def test_caps_and_minimum_never_round_up(self):
        for key, expected in [('ic_gold', 100), ('ic_us500', 250)]:
            capped = operations.risk_calculator(6000, 5999.99, 1000000, instrument_key=key)
            self.assertEqual(capped['quantity'], expected)
            self.assertTrue(capped['capped_at_maximum'])
            tiny = operations.risk_calculator(6000, 5990, 0.01, instrument_key=key)
            self.assertEqual(tiny['quantity'], 0)
            self.assertTrue(tiny['below_minimum'])

    def test_exact_step_boundary_and_stock_compatibility(self):
        result = size_cash_risk(10.1, 10, 0.3, point_value=100, quantity_step=.01, min_quantity=.01)
        self.assertEqual(result['quantity'], .03)
        self.assertLessEqual(result['actual_risk'], .3)
        stock = operations.risk_calculator(10, 9.5, 10000)
        self.assertEqual((stock['quantity'], stock['actual_risk']), (200, 100))

    def test_bad_inputs_and_wrong_stop_side(self):
        for changes in ({'entry_price': float('nan')}, {'stop_price': 10}, {'direction': 'SHORT'},
                        {'costs_per_unit': -1}, {'risk_budget': -1}, {'quantity_step': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                size_cash_risk(**{'entry_price': 10, 'stop_price': 9, 'risk_budget': 100, **changes})


class NativeCFDTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cfd-test-', dir=ROOT / 'data')
        self.addCleanup(self.temp.cleanup)
        original = db.DB_PATH
        self.addCleanup(setattr, db, 'DB_PATH', original)
        db.DB_PATH = Path(self.temp.name) / 'journal.db'
        db.init_db()
        self.account = records.save_record('accounts', {'name': 'Disposable USD demo', 'account_type': 'Demo', 'currency': 'USD'})['id']

    def spec(self, key='ic_gold', **changes):
        preset = IC_MARKETS_CONTRACTS[key]
        return neo.save_instrument_spec({'symbol': preset['symbol'], 'version': 'test-1',
            'quote_currency': 'USD', 'sizing_method': 'contract_size', 'contract_multiplier': preset['contract_size'],
            'min_quantity': preset['min_quantity'], 'max_quantity': preset['max_quantity'], 'quantity_step': preset['quantity_step'],
            'source': 'Synthetic test of published IC Markets contract inputs', 'verified_at': NOW, **changes})

    def candidate_and_basis(self, key='ic_gold'):
        preset = IC_MARKETS_CONTRACTS[key]
        session = neo.save_session({'session_key': 'test', 'account_id': self.account, 'exchange_date': '2026-10-18', 'local_date': '2026-10-18', 'timezone': 'Australia/Brisbane', 'label': 'Synthetic', 'baseline_version': 'test'})
        candidate = neo.save_candidate({'candidate_key': 'test', 'session_id': session['id'], 'symbol': preset['symbol'], 'direction': 'LONG', 'plan': {'entry_price': 6000.25, 'stop_price': 5993, 'baseline_version': 'test'}, 'reason': 'Synthetic test'})
        snapshot = neo.save_account_snapshot({'account_id': self.account, 'balance': 10000, 'currency': 'USD', 'source': 'Synthetic balance', 'observed_at': NOW, 'trading_day_timezone': 'Australia/Brisbane', 'trading_day_boundary': '17:00'})
        regime = neo.save_risk_regime({'account_id': self.account, 'mode': 'demo', 'risk_pct': 1, 'source': 'Synthetic explicit policy', 'effective_at': NOW})
        return candidate, snapshot, regime

    def test_native_sizes_without_ticks_and_preserves_plan(self):
        spec = self.spec('ic_us500')
        self.assertIsNone(spec['tick_size'])
        self.assertIsNone(spec['tick_value'])
        self.assertEqual(spec['point_value'], 1)
        candidate, snapshot, regime = self.candidate_and_basis('ic_us500')
        result = neo.size_candidate(candidate['id'], snapshot['id'], regime['id'], spec['id'], candidate['revision'], NOW)
        self.assertEqual(result['sizing']['quantity'], 13.7)
        self.assertFalse(result['sizing']['tick_validation_applied'])
        self.assertEqual(result['sizing']['sizing_method'], 'contract_size')
        plans = records.list_records('candidate_plans', filters={'candidate_id': candidate['id']})['records']
        self.assertEqual(len(plans), 2)
        self.assertEqual(json.loads(plans[-1]['plan'])['entry_price'], 6000.25)

    def test_contract_sizing_pnl_and_volume_limits(self):
        spec = self.spec()
        self.assertEqual(spec['point_value'], 100)
        trade = records.save_record('trades', {'account_id': self.account, 'symbol': 'XAUUSD.a', 'entry_price': 3000.25,
            'direction': 'LONG', 'quantity': .1, 'entry_time': NOW, 'instrument_spec_id': spec['id']})
        closed = records.trade_action('close', trade['id'], 3001.5, NOW)
        self.assertEqual(records.get_record('trades', trade['id'])['pnl'], 12.5)
        with self.assertRaisesRegex(ValueError, 'maximum'):
            records.save_record('trades', {'account_id': self.account, 'symbol': 'XAUUSD.a', 'entry_price': 3000,
                'direction': 'LONG', 'quantity': 100.01, 'entry_time': NOW, 'instrument_spec_id': spec['id']})

    def test_legacy_tick_validation_remains_and_invalid_pairs_reject(self):
        for changes in ({'point_value': 10000}, {'tick_size': .01}, {'tick_size': 0, 'tick_value': 0},
                        {'tick_size': .01, 'tick_value': .01}, {'max_quantity': .001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.spec(**changes)
        legacy = self.spec(sizing_method='point_value', point_value=100, tick_size=.25, tick_value=25)
        with self.assertRaisesRegex(ValueError, 'tick size'):
            neo.validate_trade_economics({'instrument_spec_id': legacy['id'], 'account_id': self.account,
                'symbol': legacy['symbol'], 'entry_price': 3000.01, 'quantity': 1})

    def legacy_table(self):
        # Simulate the pre-upgrade schema, including durable IDs referenced elsewhere.
        with closing(db.get_connection()) as conn, conn:
            conn.execute('DROP TABLE instrument_specs')
            old = TABLES['instrument_specs'].replace('tick_size REAL, tick_value REAL,', 'tick_size REAL NOT NULL, tick_value REAL NOT NULL,').replace("max_quantity REAL, sizing_method TEXT NOT NULL DEFAULT 'point_value',", '')
            conn.execute(f"CREATE TABLE instrument_specs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (datetime('now')), {old})")
            conn.execute("INSERT INTO instrument_specs (id,symbol,version,quote_currency,contract_multiplier,point_value,tick_size,tick_value,quantity_step,min_quantity,source,verified_at) VALUES (7,'LEGACY','1','USD',2,2,.25,.5,1,1,'Original',?)", (NOW,))

    def test_old_schema_upgrade_retains_ids_values_and_immutability(self):
        self.legacy_table()
        trade = records.save_record('trades', {'account_id': self.account, 'symbol': 'LEGACY', 'direction': 'LONG',
            'quantity': 2, 'entry_price': 10, 'entry_time': NOW, 'instrument_spec_id': 7})
        db.init_db()
        db.init_db()
        retained = records.get_record('instrument_specs', 7)
        self.assertEqual((retained['tick_size'], retained['tick_value'], retained['source']), (.25, .5, 'Original'))
        self.assertEqual(retained['sizing_method'], 'point_value')
        self.assertEqual(records.get_record('trades', trade['id'])['instrument_spec_id'], 7)
        self.assertEqual(self.spec()['id'], 8)
        with closing(db.get_connection()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE instrument_specs SET point_value=999 WHERE id=7")

    def test_failed_upgrade_rolls_back_schema_and_history(self):
        self.legacy_table()
        with patch('journal_mcp.pattern_schema.migrate', side_effect=RuntimeError('Synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic failure'):
                db.init_db()
        columns = {row['name']: row for row in db.fetch_all('PRAGMA table_info(instrument_specs)')}
        self.assertEqual(columns['tick_size']['notnull'], 1)
        self.assertNotIn('sizing_method', columns)
        self.assertEqual(db.fetch_one('SELECT point_value FROM instrument_specs WHERE id=7')['point_value'], 2)

    def test_mcp_contract_save_and_preview(self):
        from mcp import Client
        from journal_mcp.server import create_server
        async def run():
            async with Client(create_server()) as client:
                schema = (await client.call_tool('journal_schema', {'entity': 'instrument_specs'})).structured_content
                self.assertTrue(schema['capabilities']['contract_size_sizing'])
                self.assertFalse(schema['entities']['instrument_specs']['fields']['tick_size']['required'])
                result = await client.call_tool('save_instrument_spec', {'data': {'symbol': 'XAUUSD.a', 'version': 'mcp-test', 'quote_currency': 'USD', 'contract_multiplier': 100, 'sizing_method': 'contract_size', 'quantity_step': .01, 'min_quantity': .01, 'max_quantity': 100, 'source': 'Synthetic test', 'verified_at': NOW}, 'idempotency_key': 'test-contract'})
                self.assertFalse(result.is_error, result)
                self.assertIsNone(result.structured_content['tick_value'])
                preview = await client.call_tool('risk_calculator', {'entry_price': 3000, 'stop_price': 2997, 'balance': 10000, 'instrument_key': 'ic_gold'})
                self.assertEqual(preview.structured_content['quantity'], .33)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
