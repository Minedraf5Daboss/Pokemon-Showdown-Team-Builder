"""Exercise the provider against an offline HTTP transport; no paid API calls."""

import json

import httpx
from openai import APITimeoutError, AuthenticationError, OpenAI
import pytest

import llm


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for name in ('LLM_PROVIDER', 'LLM_TIMEOUT_SECONDS', 'LLM_MODEL', 'LLM_BASE_URL',
                 'LLM_API_KEY', 'LLM_TOOL_PROTOCOL', 'LLM_CHAT_MODE',
                 'LLM_CONTEXT_TOKENS', 'LLM_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(name, raising=False)


def install_transport(monkeypatch, handler):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr(
        llm, "OpenAI",
        lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )


def test_hello_world(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "model": "test-model", "instructions": "Be brief.", "input": "Hello!"
        }
        assert request.extensions["timeout"]["read"] == 30.0
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 0,
            "model": "test-model", "status": "completed",
            "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                        "status": "completed", "content": [
                            {"type": "output_text", "text": "Hello, world!", "annotations": []}
                        ]}],
        })

    install_transport(monkeypatch, handler)
    assert llm.call_llm("Be brief.", "Hello!") == "Hello, world!"
    assert len(requests) == 1


def test_timeout_retries_only_once(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    install_transport(monkeypatch, handler)
    with pytest.raises(APITimeoutError):
        llm.call_llm("Be brief.", "Hello!")
    assert len(requests) == 2


def test_auth_failure_is_not_retried(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "Invalid key"}})

    install_transport(monkeypatch, handler)
    with pytest.raises(AuthenticationError):
        llm.call_llm("Be brief.", "Hello!")
    assert len(requests) == 1


def test_missing_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Set OPENAI_API_KEY"):
        llm.call_llm("Be brief.", "Hello!")


def test_local_hello_world_without_cloud_key(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local"
        assert request.extensions["timeout"]["read"] == 120.0
        assert json.loads(request.content) == {
            "model": "my-local-model", "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hello!"},
            ],
        }
        return httpx.Response(200, json={
            "id": "chat_test", "object": "chat.completion", "created": 0,
            "model": "my-local-model", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Hello, world!"},
                 "finish_reason": "stop"}
            ],
        })

    install_transport(monkeypatch, handler)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LLM_MODEL", "my-local-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    assert llm.call_llm("Be brief.", "Hello!") == "Hello, world!"
    assert len(requests) == 1


def test_local_requires_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LLM_MODEL", "")
    with pytest.raises(RuntimeError, match="Set LLM_MODEL"):
        llm.call_llm("Be brief.", "Hello!")


def test_tool_message_round_trip_with_local_defaults(monkeypatch):
    monkeypatch.setenv('LLM_TOOL_PROTOCOL', 'native')
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body['model'] == 'llama3.1:8b'
        assert request.extensions['timeout']['read'] == 120
        assert body['tools'][0]['function']['name'] == 'get_pool'
        return httpx.Response(200, json={
            'id': 'chat_test', 'object': 'chat.completion', 'created': 0,
            'model': 'llama3.1:8b', 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call_1', 'type': 'function', 'function': {'name': 'get_pool', 'arguments': '{}'}}]}}]})
    install_transport(monkeypatch, handler)
    monkeypatch.delenv('LLM_PROVIDER')
    monkeypatch.delenv('OPENAI_API_KEY')
    tools = [{'type': 'function', 'function': {'name': 'get_pool', 'parameters': {'type': 'object'}}}]
    message = llm.chat_llm([{'role': 'user', 'content': 'List Pokemon'}], tools=tools)
    assert message['tool_calls'][0]['id'] == 'call_1'
    assert message['tool_calls'][0]['function']['arguments'] == '{}'
    assert len(seen) == 1


def test_ollama_constrained_actions_and_observations(monkeypatch):
    real_client = httpx.Client
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == 'http://localhost:11434/api/chat'
        assert body['model'] == 'llama3.1:8b'
        assert body['options']['num_ctx'] == 16384
        assert body['format']['oneOf'][0]['properties']['tool']['const'] == 'get_profiles'
        assert body['messages'][-1]['content'] == 'Program observation: {"pokemon":["Kyogre"]}'
        assert body['messages'][-2]['role'] == 'assistant'
        return httpx.Response(200, json={'done': True, 'message': {
            'role': 'assistant', 'content': '{"tool":"get_pool","arguments":{"name":"Kyogre"}}'}})
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    from team_chat import tool_definitions
    messages = [{'role': 'system', 'content': 'Use tools.'},
                {'role': 'user', 'content': 'List Pokemon'},
                {'role': 'assistant', 'tool_calls': [{'id': 'x', 'type': 'function',
                    'function': {'name': 'get_pool', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'x', 'content': '{"pokemon":["Kyogre"]}'}]
    result = llm.chat_llm(messages, tools=tool_definitions())
    assert result['tool_calls'][0]['function']['name'] == 'get_pool'
    assert json.loads(result['tool_calls'][0]['function']['arguments']) == {'name': 'Kyogre'}
    assert messages[0]['content'] == 'Use tools.'
    assert len(requests) == 1


@pytest.mark.parametrize('code, expected', [(503, 2), (401, 1)])
def test_ollama_retry_boundary(monkeypatch, code, expected):
    real_client = httpx.Client
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(code, json={'error': 'unavailable'})
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        llm.chat_llm([{'role': 'user', 'content': 'Hello'}])
    assert len(requests) == expected


def completion(content=None, *, calls=None, model='test-model'):
    message = {'role': 'assistant', 'content': content}
    if calls:
        message['tool_calls'] = calls
    return httpx.Response(200, json={
        'id': 'chat_test', 'object': 'chat.completion', 'created': 0, 'model': model,
        'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if calls else 'stop'}],
    })


@pytest.mark.parametrize('provider, base_url, key', [
    ('anthropic', 'https://api.anthropic.com/v1', 'ANTHROPIC_API_KEY'),
    ('gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'GEMINI_API_KEY'),
    ('groq', 'https://api.groq.com/openai/v1', 'GROQ_API_KEY'),
    ('openrouter', 'https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY'),
])
def test_cloud_presets_use_their_own_credentials_for_text_and_chat(monkeypatch, provider, base_url, key):
    bodies = []
    tools = [{'type': 'function', 'function': {'name': 'get_pool', 'parameters': {'type': 'object'}}}]

    def handler(request):
        assert str(request.url) == base_url + '/chat/completions'
        assert request.headers['authorization'] == 'Bearer provider-key'
        assert request.extensions['timeout']['read'] == 30
        body = json.loads(request.content)
        assert body['model'] == 'provider-model'
        bodies.append(body)
        if len(bodies) == 1:
            return completion('Hello!')
        return completion(calls=[{'id': 'call_1', 'type': 'function',
                                  'function': {'name': 'get_pool', 'arguments': '{}'}}])

    install_transport(monkeypatch, handler)
    monkeypatch.setenv('LLM_PROVIDER', provider)
    monkeypatch.setenv(key, 'provider-key')
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:9999/v1')
    monkeypatch.setenv('LLM_API_KEY', 'unrelated-key')
    monkeypatch.setenv('LLM_MODEL', 'provider-model')
    assert llm.call_llm('Be brief.', 'Hello') == 'Hello!'
    response = llm.chat_llm([{'role': 'user', 'content': 'List available Pokemon.'}], tools=tools)
    assert response['tool_calls'][0]['id'] == 'call_1'
    assert 'tools' not in bodies[0]
    assert bodies[1]['tools'] == tools


def test_tiny_compatible_model_builds_and_updates_through_prompt_actions(monkeypatch):
    from team_chat import run_chat

    bodies = []

    def handler(request):
        assert str(request.url) == 'http://localhost:1234/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer local'
        body = json.loads(request.content)
        assert body['model'] == 'tiny-model'
        assert body['max_tokens'] == 512
        assert 'tools' not in body and 'response_format' not in body
        assert all(m['role'] in {'system', 'user', 'assistant'} for m in body['messages'])
        bodies.append(body)
        if len(bodies) == 1:
            return completion('```json\n{"tool":"get_pool","arguments":{"name":"Kyogre"}}\n```')
        if len(bodies) == 2:
            assert 'Program observation: {"name": "Kyogre"' in body['messages'][-1]['content']
            return completion(json.dumps({'tool': 'build_team', 'arguments': {'request': {
                'engine': 'rain', 'required': [{'name': 'Kyogre'}], 'excluded': ['Groudon']}}}))
        assert len(bodies) == 3
        assert 'Previous request state' in body['messages'][0]['content']
        assert 'update_team' in body['messages'][0]['content']
        return completion('{"tool":"update_team","arguments":{"changes":{"preferences":{"offense":"high"}}}}')

    install_transport(monkeypatch, handler)
    monkeypatch.setenv('LLM_PROVIDER', 'compatible')
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:1234/v1/')
    monkeypatch.setenv('LLM_MODEL', 'tiny-model')
    monkeypatch.setenv('LLM_CHAT_MODE', 'compact')
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS', '512')

    history = [{'role': 'user', 'content': 'Build a rain team with Kyogre and no Groudon.'}]
    build = run_chat({'messages': history})
    assert build['status'] == 'built'
    assert build['explanation_method'] == 'deterministic evidence renderer'
    history.extend([{'role': 'assistant', 'content': build['grounded_reply']},
                    {'role': 'user', 'content': 'Change offense preference to high.'}])
    updated = run_chat({'messages': history})
    assert updated['status'] == 'built'
    receipt = updated['result']['interpretation']
    assert receipt['engine'] == 'rain'
    assert receipt['required'] == [{'name': 'Kyogre', 'set_id': None}]
    assert receipt['excluded'] == ['Groudon']
    assert receipt['resolved_preferences']['offense'] == 'high'
    assert len(bodies) == 3


def test_ollama_model_and_context_can_be_set_independently(monkeypatch):
    real_client = httpx.Client

    def handler(request):
        assert str(request.url) == 'http://localhost:11434/api/chat'
        body = json.loads(request.content)
        assert body['model'] == 'qwen2.5:0.5b'
        assert body['options']['num_ctx'] == 4096
        assert body['options']['num_predict'] == 512
        return httpx.Response(200, json={'message': {'content': '{"tool":"reply","reply":"Hi!"}'}})

    monkeypatch.setattr(llm.httpx, 'Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:11434/')
    monkeypatch.setenv('LLM_MODEL', 'qwen2.5:0.5b')
    monkeypatch.setenv('LLM_CONTEXT_TOKENS', '4096')
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS', '512')
    tools = [{'type': 'function', 'function': {'name': 'get_pool', 'parameters': {'type': 'object'}}}]
    assert llm.chat_llm([{'role': 'user', 'content': 'Hi'}], tools=tools)['content'] == 'Hi!'


@pytest.mark.parametrize('action', [
    '[]',
    '{"tool":"unknown","arguments":{}}',
    '{"tool":"get_pool","arguments":[]}',
    '{"tool":"reply","reply":""}',
])
def test_invalid_prompt_actions_are_rejected(monkeypatch, action):
    install_transport(monkeypatch, lambda request: completion(action))
    monkeypatch.setenv('LLM_TOOL_PROTOCOL', 'prompt')
    tools = [{'type': 'function', 'function': {'name': 'get_pool', 'parameters': {'type': 'object'}}}]
    with pytest.raises(RuntimeError, match='invalid tool name or action shape'):
        llm.chat_llm([{'role': 'user', 'content': 'Hi'}], tools=tools)
