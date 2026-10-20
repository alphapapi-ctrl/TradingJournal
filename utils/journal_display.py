"""Readable presentation of stored journal notes without exposing record payloads."""
import json
from datetime import datetime

import streamlit as st


def label(value):
    return str(value).replace('_', ' ').strip().capitalize()


def date_time(value):
    if not value:
        return 'Time not recorded'
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.strftime('%d %b %Y, %H:%M %z') if 'T' in str(value) or ' ' in str(value) else parsed.strftime('%d %b %Y')
    except ValueError:
        return str(value)


def show_note(value):
    """Keep prose verbatim; display structured notes as labelled fields."""
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    if isinstance(value, dict):
        for key, item in value.items():
            if item is None or item == '' or item == [] or item == {}:
                continue
            if isinstance(item, (dict, list)):
                st.markdown(f'**{label(key)}**')
                show_note(item)
            else:
                st.write(f'{label(key)}: {item}')
    elif isinstance(value, list):
        for item in value:
            show_note(item)
    elif value is not None and value != '':
        st.write(value)
