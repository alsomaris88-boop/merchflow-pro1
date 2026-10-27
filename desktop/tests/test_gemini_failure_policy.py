"""Account penalty policy: real SQLite, explicitly isolated provider doubles.

A UI/clipboard/CDP/extraction problem is not evidence of quota exhaustion.
No Google cookies, paid requests, synthetic successful accounts or renders.
"""
import base64
import io

from PIL import Image
import pytest

from desktop.worker import db, gemini_pool, output_store
from desktop.worker.gemini_client import (
    GeminiAuthError, GeminiAutomationError, GeminiRefusalError, ModelDegradedError,
)


@pytest.fixture
def policy_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "private-test.sqlite")
    db.init()
    monkeypatch.setattr(gemini_pool, "CLIENTS", {})
    monkeypatch.setattr(gemini_pool, "MOCK", False)
    pool = gemini_pool.AccountPool()
    pool.accounts = [gemini_pool.AccountState(id=i,status="ready",is_pro=True) for i in (1,2,3)]
    monkeypatch.setattr(gemini_pool,"POOL",pool)
    monkeypatch.setattr(pool,"_wait_jitter",lambda account:None)
    calls=[]
    settings={"error":None,"empty":False}
    class Provider:
        def __init__(self,key): self.key=key
        def generate_image(self,prompt,references):
            calls.append((self.key,prompt,references))
            if settings["error"]: raise settings["error"]
            assert settings["empty"], "this fixture is failure-only, never a fake success"
            return b""
    providers={i:Provider(i) for i in (1,2,3)}
    monkeypatch.setattr(gemini_pool,"client_for",providers.get)
    buffer=io.BytesIO();Image.new("RGB",(30,36),(40,50,60)).save(buffer,"PNG")
    original=buffer.getvalue()
    return pool,settings,calls,original,tmp_path / "output"


def run_path(path,original,out):
    events=[]
    if path=="donepixel":
        with pytest.raises(RuntimeError) as failure:
            gemini_pool.render_donepixel(original,"#000000",lambda label:None)
        return str(failure.value),events
    data="data:image/png;base64,"+base64.b64encode(original).decode()
    result=gemini_pool.run_batch({"styleImage":data,"background":"black",
        "nicheImages":[{"id":"real-test-job","name":"reference","image":data}]},events.append,out)
    assert result=={"completed":0,"failed":1}
    assert not any(event["type"]=="image" for event in events)
    failed=[event for event in events if event["type"]=="failed"]
    assert len(failed)==1
    assert not output_store.list_designs(out) and not list(out.glob("*.png"))
    return failed[0]["reason"],events


def test_parallel_donepixel_bypasses_serial_jitter(policy_pool, monkeypatch):
    pool, settings, _, original, out = policy_pool
    settings["empty"] = True  # fixture-only failure; never fabricate a successful render
    acquire = pool.acquire
    parallel_modes = []
    jitter_calls = []

    def tracked_acquire(*args, **kwargs):
        parallel_modes.append(kwargs.get("parallel"))
        return acquire(*args, **kwargs)

    monkeypatch.setattr(pool, "acquire", tracked_acquire)
    monkeypatch.setattr(pool, "_wait_jitter", lambda account: jitter_calls.append(account.id))
    reason, _ = run_path("donepixel", original, out)

    assert reason == "Provider returned no image"
    assert parallel_modes == [True]
    assert jitter_calls == []


@pytest.mark.parametrize("path",["donepixel","fusion"])
@pytest.mark.parametrize("error",[
    GeminiAutomationError("GATE 1 FAILED: no real reference thumbnail"),
    GeminiAutomationError("GATE 2 FAILED: reference not accepted"),
    GeminiAutomationError("CDP tab setup failed: local browser disconnected"),
    GeminiAutomationError("Prompt injection did not preserve the complete 8K directive"),
    GeminiAutomationError("Original unavailable; preview/canvas fallback is disabled"),
    GeminiRefusalError("Gemini refused this request; not a quota or account failure"),
])
def test_local_or_refused_failures_do_not_cooldown_or_retry_another_account(policy_pool,path,error):
    pool,settings,calls,original,out=policy_pool
    settings["error"]=error
    reason,events=run_path(path,original,out)
    assert reason==str(error)
    assert len(calls)==1, "an uncertain paid render must never silently reroute"
    assert not pool._busy
    for account in pool.accounts:
        assert account.status=="ready" and account.is_pro and account.cooldown_until==0
        assert account.window_usage==account.total_generated==account.daily_used==account.weekly_used==0
    assert db.list_accounts()==[], "test-only provider slots never become real connected accounts"
    assert not output_store.list_designs(out)


@pytest.mark.parametrize("path",["donepixel","fusion"])
def test_empty_result_is_failed_once_without_false_cooldown_or_success_usage(policy_pool,path):
    pool,settings,calls,original,out=policy_pool
    settings["empty"]=True
    reason,_=run_path(path,original,out)
    assert reason=="Provider returned no image"
    assert len(calls)==1 and not pool._busy
    assert all(a.status=="ready" and a.is_pro and a.cooldown_until==a.window_usage==a.total_generated==0 for a in pool.accounts)
    assert not output_store.list_designs(out)


@pytest.mark.parametrize("path",["donepixel","fusion"])
@pytest.mark.parametrize("error,status",[
    (GeminiAuthError("Verified Google sign-out: session expired"),"auth-required"),
    (ModelDegradedError("Explicit provider image limit reached"),"cooldown"),
])
def test_real_auth_and_quota_signals_still_enforce_existing_account_guards(policy_pool,path,error,status):
    pool,settings,calls,original,out=policy_pool
    pool.accounts=pool.accounts[:1]
    settings["error"]=error
    reason,_=run_path(path,original,out)
    assert reason==str(error) and len(calls)==1
    [account]=pool.accounts
    assert account.status==status and not pool._busy
    if status=="cooldown":
        assert account.cooldown_until>0 and account.window_usage>=gemini_pool.WINDOW_CAP and account.is_pro
    else: assert not account.is_pro
    assert not output_store.list_designs(out) and db.list_accounts()==[]
