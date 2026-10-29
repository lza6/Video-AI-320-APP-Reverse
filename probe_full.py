"""R2 最后一枪：完全按 OutputVideo.toJson() 形状构造任务文档（含 id/status/inputType/width/height/usedCreditCount）。"""
import json,os,secrets,ssl,string,time,hashlib,urllib.request,urllib.error
PROXY=os.getenv("VAPI_PROXY","http://127.0.0.1:10808"); KEY=os.getenv("FIREBASE_API_KEY","")
P=os.getenv("FIREBASE_PROJECT","video-app-codeway"); SALT=os.getenv("VIDEOAI_SIG_SECRET","")
B=f"https://firestore.googleapis.com/v1/projects/{P}/databases/(default)/documents"
op=urllib.request.build_opener(urllib.request.ProxyHandler({"http":PROXY,"https":PROXY}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
def sig(cid,ts): return hashlib.sha256(f"{SALT}_{cid}_{ts}".encode()).hexdigest().upper()
def call(u,body=None,method="GET"):
    r=urllib.request.Request(u,data=json.dumps(body).encode() if body is not None else None,method=method,headers={"content-type":"application/json"})
    try:
        with op.open(r,timeout=25) as resp: return resp.status,resp.read()
    except urllib.error.HTTPError as e: return e.code,e.read()
    except Exception as e: return 0,repr(e).encode()
cid=str(secrets.token_hex(16))
A=string.ascii_letters+string.digits
nid=lambda n=20:"".join(secrets.choice(A) for _ in range(n))
ts=int(time.time()*1000)
# 1) 账号记录（credits=0，不伪造余额）
st,_=call(f"{B}/cerebro-users/{cid}?key={KEY}",{"fields":{"cerebroId":{"stringValue":cid},"createdAt":{"integerValue":str(ts)},
    "updatedAt":{"integerValue":str(ts)},"credits":{"integerValue":"0"},"sig":{"stringValue":sig(cid,ts)}}},"PATCH")
print("user create:",st,flush=True)
# 2) 完整形状任务
did=nid(); jts=int(time.time()*1000)
F={"id":{"stringValue":did},"inputType":{"stringValue":"text"},"status":{"stringValue":"CREATED"},
   "width":{"integerValue":"512"},"height":{"integerValue":"512"},"createdAt":{"integerValue":str(jts)},
   "cerebroId":{"stringValue":cid},"prompts":{"arrayValue":{"values":[{"stringValue":"a neon cat walking in rain"}]}},
   "usedCreditCount":{"integerValue":"1"},"aiModel":{"stringValue":"txt2vid"},
   "txt2vidModel":{"stringValue":"fal_ai_stable_video"},"guidanceScale":{"doubleValue":7.5},
   "numInferenceSteps":{"integerValue":"30"},"numFrames":{"integerValue":"121"},
   "sig":{"stringValue":sig(cid,jts)}}
st,raw=call(f"{B}/videos/{did}?key={KEY}",{"fields":F},"PATCH")
print("job create:",st,did,flush=True)
if st!=200: print(raw[:200]); raise SystemExit
t0=time.time()
for i in range(10):
    time.sleep(15)
    st,raw=call(f"{B}/videos/{did}?key={KEY}")
    try:
        d=json.loads(raw); f=d.get("fields",{})
        print(f"[{int(time.time()-t0)}s] status={(f.get('status') or {}).get('stringValue')} "
              f"err={(f.get('error') or {}).get('stringValue')} upd={d.get('updateTime')}",flush=True)
    except Exception: print(f"[{int(time.time()-t0)}s] {st}",flush=True)
