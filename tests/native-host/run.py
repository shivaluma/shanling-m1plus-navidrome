#!/usr/bin/env python3
"""Compile/run actual native C plugin with mock exported toolkit and HTTP fixture."""
import http.server
import json
import os
import platform
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.parse

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent

def main():
    with tempfile.TemporaryDirectory(prefix="nd-native-host-") as temp:
        work=Path(temp);state=work/"state";state.write_text("normal")
        requests=[]
        artists=[{"id":f"ar{i}","name":"宇多田 Test" if i==0 else f"Artist {i}","album_count":2} for i in range(6)]
        albums=[{"id":f"al{i}","name":"Requiem café" if i==0 else f"Album {i}","artist":"宇多田 Test","artist_id":"ar0","song_count":6} for i in range(6)]
        tracks=[{"id":f"s{i}","title":f"Track {i}","album":"Requiem café","artist":"宇多田 Test","track":i+1,"disc":2,"duration":120} for i in range(6)]
        jobs={};saved=set();prepare_batches=[]
        def offline_summary(key,items):
            j=jobs.get(key,{})
            return {"downloaded":sum(t["id"] in saved for t in items),"total":len(items),"state":"" if j.get("state")=="removed" else j.get("state",""),"done":j.get("done",0),"error":j.get("error","")}
        class Fixture(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,value,status=200):
                data=json.dumps(value,ensure_ascii=False).encode() if not isinstance(value,bytes) else value
                self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query);requests.append((u.path,q));mode=state.read_text()
                if mode=="dlfail" and u.path!="/v1/downloads":mode="normal"
                if mode=="delay":Path(str(state)+".inflight").touch();time.sleep(1)
                if mode=="malformed":self.respond(b"{broken");return
                if mode=="schema":self.respond({"artists":"invalid shape"});return
                if mode=="error":self.respond({"error":{"message":"fixture failure"}},503);return
                if mode=="empty":self.respond({"artists":[]});return
                if u.path=="/v1/artists":self.respond({"artists":artists})
                elif u.path=="/v1/artist":self.respond({"artist":artists[0],"albums":albums[:2]})
                elif u.path=="/v1/albums":
                    o=int(q.get("offset",["0"])[0]);n=int(q.get("size",["4"])[0]);self.respond({"albums":albums[o:o+n],"offset":o,"size":n})
                elif u.path=="/v1/album":self.respond({"album":albums[0],"tracks":[dict(t,downloaded=t["id"] in saved) for t in tracks],"offline":offline_summary("album:al0",tracks)})
                elif u.path=="/v1/downloads":
                    for j in jobs.values():
                        if j["state"]=="queued":j["state"]="active";j["done"]=3;j["bytes"]=3000
                        elif j["state"]=="active" and mode=="dlfail":j["state"]="failed";j["error"]="not enough space on the SD card"
                        elif j["state"]=="active":j.update(state="done",done=6,bytes=6000);saved.update(t["id"] for t in tracks)
                    self.respond({"jobs":list(reversed(list(jobs.values()))),"free_bytes":12884901888,"reserve_bytes":524288000,"folder":"Navidrome","downloaded_tracks":len(saved)})
                elif u.path=="/v1/search":
                    assert q.get("q")==["宇多田 & café"],"native UTF-8 query was corrupted"
                    o=int(q.get("offset",["0"])[0]);n=int(q.get("size",["4"])[0]);self.respond({"artists":artists[o:o+n],"albums":albums[o:o+n],"tracks":tracks[o:o+n],"offset":o,"size":n})
                elif u.path=="/v1/playlists":self.respond({"playlists":[{"id":"pl","name":"Fixture Mix","song_count":3}]})
                elif u.path=="/v1/playlist":self.respond({"playlist":{"id":"pl","name":"Fixture Mix"},"tracks":[dict(t,downloaded=t["id"] in saved) for t in tracks[:3]],"offline":offline_summary("playlist:pl",tracks[:3])})
                else:self.respond({},404)
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))));requests.append((self.path,{"id":data["ids"] if "ids" in data else [data["id"]]}))
                if self.path in ("/v1/downloads","/v1/downloads/cancel","/v1/offline/remove"):
                    assert data["kind"] in ("album","playlist") and set(data)=={"kind","id"},"bad download request"
                    key=data["kind"]+":"+data["id"]
                    if self.path=="/v1/downloads":
                        jobs[key]=dict(id=key,kind=data["kind"],source_id=data["id"],name="Requiem café" if data["kind"]=="album" else "Fixture Mix",state="queued",total=6,done=0,bytes=0,total_bytes=6000,current="",error="")
                        self.respond({"job":jobs[key]},202)
                    elif self.path=="/v1/downloads/cancel":
                        ok=key in jobs and jobs[key]["state"] in ("queued","active")
                        if ok:jobs[key]["state"]="cancelled"
                        self.respond({"cancelled":ok})
                    else:
                        n=len(saved);saved.clear()
                        if key in jobs:jobs[key]["state"]="removed"
                        self.respond({"removed_files":n})
                    return
                if state.read_text()=="delay_prepare":Path(str(state)+".inflight").touch();time.sleep(1)
                assert set(data)=={"ids"} and 1<=len(data["ids"])<=128,"plugin must batch prepare"
                if state.read_text()=="badurl":self.respond({"urls":["https://user:secret@foreign.invalid/track"]*len(data["ids"])});return
                prepare_batches.append(list(data["ids"]))
                self.respond({"urls":["http://127.0.0.1:17831/stream/"+("%032x" % (int(i[1:])+1)) for i in data["ids"]]})
        http.server.ThreadingHTTPServer.allow_reuse_address=True
        server=http.server.ThreadingHTTPServer(("127.0.0.1",17831),Fixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        sanitizers=os.environ.get("ND_SANITIZERS","undefined")
        common=["clang","-g","-O1","-fsanitize="+sanitizers,"-fno-omit-frame-pointer","-Wno-deprecated-declarations"]
        darwin=platform.system()=="Darwin"
        plugin=work/("native-host.dylib" if darwin else "native-host.so");harness=work/"harness"
        subprocess.run(common+(["-dynamiclib"] if darwin else ["-shared","-fPIC"])+["-DND_HOST_TEST","-DND_PAGE=4","-DND_POLL_SECONDS=1","-fvisibility=hidden",str(ROOT/"native/nd_ui.c"),str(ROOT/"native/startup.c"),str(ROOT/"vendor/cJSON.c"),"-lcurl","-lpthread","-o",str(plugin)],check=True)
        subprocess.run(common+[str(HERE/"harness.c")]+(["-Wl,-export_dynamic"] if darwin else ["-rdynamic","-ldl"])+["-lpthread","-o",str(harness)],check=True)
        env=dict(os.environ,ASAN_OPTIONS="detect_leaks=0:abort_on_error=1",UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        try:
            result=subprocess.run([str(harness),str(plugin),str(state),str(HERE/"host-mock.svg")],env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
            (HERE/"results.txt").write_text("Sanitizers: "+sanitizers+"\n"+result.stdout+result.stderr)
            (HERE/("results-"+platform.system().lower()+".txt")).write_text("Sanitizers: "+sanitizers+"\n"+result.stdout+result.stderr)
            print(result.stdout,end="")
            if result.stderr:print(result.stderr,end="")
            assert any(p=="/v1/search" and q.get("offset")==["4"] for p,q in requests)
            assert any(p=="/v1/prepare" for p,q in requests)
            assert prepare_batches and prepare_batches[0]==["s0","s1","s2","s3","s4","s5"],"album queue prepared in one request"
            (HERE/"requests.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2)+"\n")
        except subprocess.CalledProcessError as err:
            print(err.stdout or "",end="");print(err.stderr or "",end="");raise
        except subprocess.TimeoutExpired as err:
            print((err.stdout or b"").decode(errors="replace"),end="");print((err.stderr or b"").decode(errors="replace"),end="");raise
        finally:server.shutdown();server.server_close()

if __name__=="__main__":main()
