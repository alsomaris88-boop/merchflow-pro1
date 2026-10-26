"""The shared HTTP Gemini session may pipeline distinct per-job RPC requests.

No Google credentials/network/provider calls: captured local request fixtures only.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
from types import SimpleNamespace

from desktop.worker.gemini_client import GeminiClient


def test_one_http_account_can_run_eight_independent_image_rpcs_concurrently(monkeypatch):
    workers=8
    rendezvous=threading.Barrier(workers)
    lock=threading.Lock()
    active=0
    peak=0
    reqids=[]
    prompts=[]
    client=GeminiClient("TEST-ONLY-NO-COOKIE")
    client.token="TEST-ONLY-SNLM0E"
    client.bl="fixture-build"
    client.f_sid="12345"

    class StubSession:
        def post(self,url,*,params,data,headers,timeout):
            nonlocal active,peak
            envelope=json.loads(data["f.req"])
            prompt=json.loads(envelope[1])[0][0]
            with lock:
                active+=1;peak=max(peak,active)
                reqids.append(params["_reqid"]);prompts.append(prompt)
            try:
                rendezvous.wait(timeout=8)
                suffix=re.search(r"REQUEST-(\d+)$",prompt).group(1)
                return SimpleNamespace(status_code=200,text=f"https://lh3.googleusercontent.com/gg-dl/fixture-{suffix}")
            finally:
                with lock: active-=1

    monkeypatch.setattr(client,"client",StubSession())
    monkeypatch.setattr(client,"_download",lambda url:url.rsplit("-",1)[-1].encode())
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures=[executor.submit(client.generate_image,f"REQUEST-{index}",[]) for index in range(workers)]
            outputs=[future.result(timeout=15) for future in futures]
        assert peak==workers, "one account's HTTP transport serialized independent image jobs"
        assert sorted(outputs)==[str(i).encode() for i in range(workers)]
        assert len(set(reqids))==workers and len(set(prompts))==workers
    finally:
        # StubSession intentionally does not have a live HTTP connection to close.
        pass
