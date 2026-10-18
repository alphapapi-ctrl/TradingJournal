"""Streamlit interactions against disposable journals, never the user's history."""
import json
import tempfile
import unittest
from pathlib import Path

import database as db
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class CalculatorUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cfd-ui-', dir=ROOT / 'data')
        self.addCleanup(self.temp.cleanup)
        original = db.DB_PATH
        self.addCleanup(setattr, db, 'DB_PATH', original)
        self.path = Path(self.temp.name) / 'journal.db'
        db.DB_PATH = self.path
        db.init_db()
        db.execute("INSERT INTO accounts (name,broker,currency,account_type,initial_balance) VALUES ('Synthetic USD demo','IC Markets','USD','Demo',999999)")
        self.app = AppTest.from_string(f"""
import database as db
from pathlib import Path
db.DB_PATH = Path({str(self.path)!r})
from pages.risk_calculator import _position_sizer
_position_sizer()
""", default_timeout=20).run()
        self.assertFalse(self.app.exception)

    def enter(self, balance, entry, stop, costs=0):
        self.app.number_input(key='rc_balance').set_value(balance)
        self.app.number_input(key='rc_entry').set_value(entry)
        self.app.number_input(key='rc_stop').set_value(stop)
        self.app.number_input(key='rc_costs').set_value(costs)
        self.app.run()
        self.assertFalse(self.app.exception)

    def metric(self, label):
        return next(metric.value for metric in self.app.metric if metric.label == label)

    def test_cfd_dropdown_gold_and_us500_decimal_prices(self):
        self.app.selectbox(key='rc_asset_type').select('CFD').run()
        self.assertFalse(self.app.exception)
        self.enter(10000, 3000, 2997)
        self.assertEqual(self.metric('Position Size'), '0.33 lots')
        self.app.selectbox(key='rc_cfd_contract').select('ic_us500').run()
        self.enter(10000, 6000.25, 5993)
        self.assertEqual(self.metric('Position Size'), '13.70 lots')
        self.assertEqual(self.metric('Planned risk (USD)'), '99.33')
        self.assertNotIn('rc_fetch', [button.key for button in self.app.button])
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM trades')['n'], 0)

    def test_save_load_restores_cfd_contract_and_costs_and_plan_is_not_fill(self):
        self.app.selectbox(key='rc_asset_type').select('CFD').run()
        self.enter(10000, 3000, 2997, 7)
        self.app.button(key='rc_save_setup').click().run()
        saved = db.fetch_one('SELECT * FROM risk_setups')
        self.assertEqual((saved['instrument_key'], saved['shares'], saved['costs_per_unit']), ('ic_gold', .32, 7))
        self.app.selectbox(key='rc_asset_type').select('Normal stock').run()
        self.app.button(key='rc_load_btn').click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key='rc_asset_type').value, 'CFD')
        self.assertEqual(self.app.number_input(key='rc_costs').value, 7)
        self.assertEqual(self.metric('Position Size'), '0.32 lots')
        self.app.button(key='rc_create_plan').click().run()
        self.assertFalse(self.app.exception)
        plan = db.fetch_one('SELECT * FROM journal_entries')
        self.assertIn('0.32 lots', plan['pre_plan'])
        self.assertEqual(json.loads(plan['custom_fields'])['position_sizing']['contract_size'], 100)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM trades')['n'], 0)

    def test_stock_sizing_and_wrong_side_stop(self):
        self.app.text_input(key='rc_ticker').set_value('SYNTH')
        self.enter(10000, 10, 9.5)
        self.assertEqual(self.metric('Position Size'), '200 shares')
        self.app.selectbox(key='rc_dir').select('SHORT').run()
        self.assertTrue(any('above a short' in warning.value for warning in self.app.warning))

    def test_non_usd_account_does_not_silently_size_usd_cfd(self):
        db.execute("UPDATE accounts SET currency='AUD'")
        self.app.selectbox(key='rc_asset_type').select('CFD').run()
        self.enter(10000, 3000, 2997)
        self.assertTrue(any('Select a USD account' in warning.value for warning in self.app.warning))
        self.assertFalse(any(metric.label == 'Position Size' for metric in self.app.metric))

    def test_legacy_stock_setup_still_loads_as_shares(self):
        db.execute("INSERT INTO risk_setups (ticker,direction,entry_price,stop_price,risk_pct,balance,shares) VALUES ('SYNTH','LONG',10,9.5,1,10000,200)")
        self.app.run()
        self.app.button(key='rc_load_btn').click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key='rc_asset_type').value, 'Normal stock')
        self.assertEqual(self.metric('Position Size'), '200 shares')


if __name__ == '__main__':
    unittest.main()
