"""The VIES client (accounts.vies) and the HTTP helper under it, with the fake of
conftest.py at the fetch function (epic-doctor-profile entry 6)."""
import json
import logging
import urllib.error

import pytest

from accounts import http, vies


def test_a_valid_and_an_invalid_answer(fake_vies):
    assert vies.fetch_vies('DE', '136695976') == vies.VALID
    fake_vies.status = 'invalid'
    assert vies.fetch_vies('DE', '136695976') == vies.INVALID
    assert fake_vies.calls == [('DE', '136695976')] * 2


@pytest.mark.parametrize('answer', [
    {'valid': False, 'userError': 'MS_UNAVAILABLE'},
    {'valid': False, 'userError': 'SERVICE_UNAVAILABLE'},
    {'userError': 'MS_MAX_CONCURRENT_REQ'}, {'userError': 'GLOBAL_MAX_CONCURRENT_REQ'},
    {'userError': 'TIMEOUT'},
    {'actionSucceed': False, 'errorWrappers': [{'error': 'MS_UNAVAILABLE'}]},
    {}, {'valid': 'yes'}, [], 'text'])
def test_unavailability_and_unreadable_answers_are_unavailable(fake_vies, answer):
    fake_vies.answer = answer
    assert vies.fetch_vies('DE', '1') == vies.UNAVAILABLE


def test_a_rejected_input_is_invalid(fake_vies):
    fake_vies.answer = {'actionSucceed': False, 'errorWrappers': [{'error': 'INVALID_INPUT'}]}
    assert vies.fetch_vies('DE', 'x') == vies.INVALID


def test_a_vow_error_body_is_unavailable(fake_vies):
    fake_vies.answer = {'errorWrappers': [{'error': 'VOW-ERR-1'}]}
    assert vies.fetch_vies('DE', '1') == vies.UNAVAILABLE


@pytest.mark.parametrize('error', [
    TimeoutError(), OSError('reset'), ValueError('bad'),
    urllib.error.HTTPError('u', 503, 'x', {}, None), urllib.error.HTTPError('u', 429, 'x', {}, None),
    json.JSONDecodeError('x', 'y', 0)])
def test_a_failed_fetch_is_unavailable_and_the_number_is_never_logged(fake_vies, error, caplog):
    fake_vies.error = error
    with caplog.at_level(logging.DEBUG):
        assert vies.fetch_vies('DE', '136695976') == vies.UNAVAILABLE
    assert '136695976' not in caplog.text


def test_post_json_sends_json(monkeypatch, settings):
    seen = {}

    def fake_fetch(request):
        seen['url'], seen['body'], seen['headers'] = (
            request.full_url, request.data, dict(request.header_items()))
        return '{"valid": true}'

    monkeypatch.setattr(http, '_fetch_text', fake_fetch)
    assert http.post_json('https://example.test/x', {'a': 1}) == '{"valid": true}'
    assert json.loads(seen['body']) == {'a': 1}
    assert seen['headers']['Content-type'] == 'application/json'


def test_post_check_posts_to_the_configured_url(monkeypatch, settings, fake_vies):
    settings.VIES_URL = 'https://vies.example/check'
    sent = {}
    monkeypatch.setattr(vies, 'post_json', lambda url, payload: sent.update(
        url=url, payload=payload) or '{"valid": false}')
    assert fake_vies.real_post_check({'countryCode': 'DE', 'vatNumber': '1'}) == {'valid': False}
    assert sent['url'] == 'https://vies.example/check'
