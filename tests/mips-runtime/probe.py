#!/usr/bin/env python3
"""Run actual MIPS service under QEMU against a disposable verified-TLS fixture."""
import collections
import hashlib
import http.server
import json
import os
import pathlib
import re
import secrets
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=pathlib.Path('/work')
RESULT=ROOT/'tests/mips-runtime/results.json'
BASE='http://127.0.0.1:17831'
PAYLOAD=b'fLaC'+bytes(range(256))*4096  # Transport fixture only, not decoder-valid audio.
USER='fixture-'+secrets.token_hex(8)
PASSWORD=secrets.token_urlsafe(24)+' 日本語'
ARTIST='artist 日本語 & café'
ALBUM='album 夜曲'
TRACK='track 光'
requests=collections.Counter()
salts=set()
fixture_errors=[]
checks=[]


def require(condition,label):
    if not condition:
        raise AssertionError(label)


def passed(label):
    checks.append(label)
    print('PASS '+label,flush=True)


class Fixture(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):
        pass  # Never log authenticated URLs, tokens, or credentials.
    def respond(self,status,data,content_type='application/json',headers=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        for key,value in (headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        if self.command!='HEAD':
            self.wfile.write(data)
    def envelope(self,part):
        body={'subsonic-response':{'status':'ok','version':'1.16.1',**part}}
        self.respond(200,json.dumps(body,ensure_ascii=False).encode())
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        try:
            parsed=urllib.parse.urlparse(self.path)
            query=urllib.parse.parse_qs(parsed.query)
            q=lambda key:query.get(key,[''])[0]
            salt=q('s')
            require(re.fullmatch('[a-f0-9]{32}',salt) is not None,'salt syntax')
            require(salt not in salts,'fresh auth salt')
            salts.add(salt)
            require(q('t')==hashlib.md5((PASSWORD+salt).encode()).hexdigest(),'auth token MD5')
            require(q('u')==USER and not q('p'),'token-only authentication')
            require(q('v')=='1.16.1' and q('c')=='ShanlingNavidrome' and q('f')=='json','protocol metadata')
            require(parsed.path.startswith('/music/rest/'),'base path preserved')
            endpoint=parsed.path.removeprefix('/music/rest/').removesuffix('.view')
            requests[endpoint]+=1
            artist={'id':ARTIST,'name':'宇多田 光 & café','albumCount':1}
            album={'id':ALBUM,'name':'夜曲 café','artist':'宇多田 光 & café','artistId':ARTIST,'songCount':1}
            track={'id':TRACK,'title':'光 — bài hát','artist':'宇多田 光 & café','album':'夜曲 café','artistId':ARTIST,'albumId':ALBUM,'duration':1,'track':2,'discNumber':1,'contentType':'audio/flac'}
            if endpoint=='getArtists':
                self.envelope({'artists':{'index':[{'name':'宇','artist':[artist]}]}})
            elif endpoint=='getArtist':
                require(q('id')==ARTIST,'artist identifier preserved')
                self.envelope({'artist':{**artist,'album':[album]}})
            elif endpoint=='getAlbumList2':
                require(q('type')=='alphabeticalByName' and q('offset')=='30' and q('size')=='7','album pagination')
                self.envelope({'albumList2':{'album':[album]}})
            elif endpoint=='getAlbum':
                if q('id')=='malformed':
                    self.respond(200,b'{invalid')
                elif q('id')=='auth-error':
                    self.respond(200,json.dumps({'subsonic-response':{'status':'failed','error':{'code':40,'message':PASSWORD+' '+q('t')}}}).encode())
                elif q('id')=='http-error':
                    self.respond(503,PASSWORD.encode(),'text/plain')
                else:
                    require(q('id')==ALBUM,'album identifier preserved')
                    self.envelope({'album':{**album,'song':[track]}})
            elif endpoint=='search3':
                require(q('query')=='宇多田 光 & café','Unicode search query')
                for prefix in ('artist','album','song'):
                    require(q(prefix+'Offset')=='15' and q(prefix+'Count')=='7','search pagination')
                self.envelope({'searchResult3':{'artist':[artist],'album':[album],'song':[track]}})
            elif endpoint=='getPlaylists':
                self.envelope({'playlists':{'playlist':[{'id':'mix','name':'夜の mix','songCount':1}]}})
            elif endpoint=='getPlaylist':
                require(q('id')=='mix','playlist identifier')
                self.envelope({'playlist':{'id':'mix','name':'夜の mix','songCount':1,'entry':[track]}})
            elif endpoint=='stream':
                require(q('format')=='raw','raw streaming requested')
                if q('id')=='audio-error':
                    self.respond(200,json.dumps({'subsonic-response':{'status':'failed','error':{'code':40,'message':PASSWORD}}}).encode())
                    return
                require(q('id')==TRACK,'song identifier preserved')
                data=PAYLOAD
                headers={'Accept-Ranges':'bytes','ETag':'"fixture-v1"'}
                range_value=self.headers.get('Range')
                if range_value:
                    match=re.fullmatch(r'bytes=(\d+)-(\d*)',range_value)
                    require(match is not None,'single valid forwarded Range')
                    lo=int(match[1]);hi=int(match[2]) if match[2] else len(data)-1
                    if lo==17:
                        require(self.headers.get('If-Range')=='"fixture-v1"','If-Range forwarded unchanged')
                    if lo>=len(data):
                        headers['Content-Range']='bytes */'+str(len(data))
                        self.respond(416,b'fixture range error','text/plain',headers)
                        return
                    hi=min(hi,len(data)-1)
                    headers['Content-Range']=f'bytes {lo}-{hi}/{len(data)}'
                    data=data[lo:hi+1]
                    status=206
                else:
                    status=200
                self.respond(status,data,'audio/flac',headers)
            else:
                self.respond(404,b'{}')
        except Exception as error:
            fixture_errors.append(str(error))
            try:self.respond(500,b'fixture assertion failed','text/plain')
            except Exception:pass


def request(path,method='GET',body=None,headers=None):
    raw=json.dumps(body,ensure_ascii=False).encode() if isinstance(body,dict) else body
    req=urllib.request.Request(BASE+path if path.startswith('/') else path,data=raw,method=method,headers=headers or {})
    if body is not None and 'Content-Type' not in req.headers:
        req.add_header('Content-Type','application/json')
    try:
        response=urllib.request.urlopen(req,timeout=25)
    except urllib.error.HTTPError as error:
        response=error
    with response:
        return response.status,dict(response.headers),response.read()


def json_request(path,status=200,**kwargs):
    code,headers,data=request(path,**kwargs)
    require(code==status,f'local HTTP expected {status}, received {code}')
    require(PASSWORD.encode() not in data and USER.encode() not in data,'credentials redacted from local response')
    return json.loads(data)


def start(config,directory):
    config_path=directory/'config.json'
    config_path.write_text(json.dumps(config,ensure_ascii=False))
    config_path.chmod(0o600)
    log=(directory/'service.log').open('w+')
    process=subprocess.Popen(['qemu-mipsel','-cpu','P5600',str(ROOT/'dist/navidrome-service'),'-config',str(config_path),'-ui-dir',''],stdout=log,stderr=log)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise AssertionError('MIPS service terminated before readiness')
        try:
            if json_request('/v1/status')['configured']:
                return process,log
        except (OSError,urllib.error.URLError):
            time.sleep(.1)
    raise AssertionError('MIPS service readiness timed out')


def stop(process,log):
    process.terminate()
    try:process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill();process.wait()
    log.flush();log.seek(0)
    output=log.read();log.close()
    require(PASSWORD not in output and USER not in output,'no credentials in process log')
    require(process.returncode==0,'clean MIPS service termination')


def main():
    binary=(ROOT/'dist/navidrome-service').read_bytes()
    original=(ROOT/'dist/unadjusted/navidrome-service').read_bytes()
    adjustment=json.loads((ROOT/'dist/backend-nan2008-adjustment.json').read_text())
    require(hashlib.sha256(binary).hexdigest()==adjustment['adjusted_sha256'],'adjusted hash matches report')
    require(hashlib.sha256(original).hexdigest()==adjustment['original_sha256'],'original hash matches report')
    require(original[:36]==binary[:36] and original[40:]==binary[40:],'only ELF e_flags changed; all machine code unchanged')
    with tempfile.TemporaryDirectory(prefix='m1plus-original-') as baseline_dir:
        baseline=pathlib.Path(baseline_dir)/'unadjusted'
        baseline.write_bytes(original);baseline.chmod(0o755)
        failed=subprocess.run(['qemu-mipsel','-cpu','P5600',str(baseline),'-h'],capture_output=True,text=True,timeout=10)
    require(failed.returncode!=0 and 'NaN mode not supported' in failed.stderr,'unadjusted MIPS NaN incompatibility reproduces')
    passed('unadjusted NaN mode rejected on P5600')
    usage=subprocess.run(['qemu-mipsel','-cpu','P5600',str(ROOT/'dist/navidrome-service'),'-h'],capture_output=True,text=True,timeout=10)
    require(usage.returncode==0 and '-config' in usage.stderr,'adjusted MIPS ELF starts and parses flags')
    passed('adjusted ELF starts; bytes outside e_flags unchanged')
    with tempfile.TemporaryDirectory(prefix='m1plus-runtime-') as temp:
        directory=pathlib.Path(temp)
        cert,key=directory/'cert.pem',directory/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        fixture=http.server.ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2;tls.load_cert_chain(cert,key)
        fixture.socket=tls.wrap_socket(fixture.socket,server_side=True)
        thread=threading.Thread(target=fixture.serve_forever,daemon=True);thread.start()
        config={'server_url':f'https://127.0.0.1:{fixture.server_port}/music/','username':USER,'password':PASSWORD,'ca_file':str(cert)}
        process,log=start(config,directory)
        try:
            artists=json_request('/v1/artists')['artists'];require(artists[0]['id']==ARTIST and artists[0]['name']=='宇多田 光 & café','artist Unicode JSON');passed('verified HTTPS authentication and Unicode artist list')
            artist=json_request('/v1/artist?'+urllib.parse.urlencode({'id':artists[0]['id']}));require(artist['albums'][0]['id']==ALBUM,'artist to album navigation');passed('artist → album navigation')
            album=json_request('/v1/album?'+urllib.parse.urlencode({'id':artist['albums'][0]['id']}));require(album['tracks'][0]['id']==TRACK and album['tracks'][0]['disc']==1 and album['tracks'][0]['track']==2,'album to track normalization');passed('album → track navigation')
            page=json_request('/v1/albums?offset=30&size=7');require(page['offset']==30 and page['size']==7,'album pagination response');passed('album pagination')
            search=json_request('/v1/search?'+urllib.parse.urlencode({'q':'宇多田 光 & café','offset':15,'size':7}));require(search['tracks'][0]['title']=='光 — bài hát' and search['artists'][0]['id']==ARTIST,'search data normalization');passed('Unicode search and pagination')
            lists=json_request('/v1/playlists')['playlists'];require(lists[0]['id']=='mix','playlist list');playlist=json_request('/v1/playlist?id=mix');require(playlist['tracks'][0]['id']==TRACK,'playlist tracks');passed('playlist browsing')
            prepared=json_request('/v1/prepare',method='POST',body={'id':TRACK});url=prepared['url'];require(url.startswith(BASE+'/stream/') and len(url.rsplit('/',1)[1])==32 and TRACK not in url,'opaque stream capability');passed('track preparation produces opaque local URL')
            code,headers,data=request(url);require(code==200 and data==PAYLOAD and headers.get('Content-Type')=='audio/flac','full audio bytes');passed('full 1 MiB stream byte equality')
            code,headers,data=request(url,headers={'Range':'bytes=17-1000','If-Range':'"fixture-v1"'});require(code==206 and data==PAYLOAD[17:1001] and headers.get('Content-Range')==f'bytes 17-1000/{len(PAYLOAD)}','range bytes/headers');passed('Range 206 forwarding and exact bytes')
            code,headers,data=request(url,method='HEAD');require(code==200 and data==b'' and int(headers.get('Content-Length','0'))==len(PAYLOAD),'HEAD length/body');passed('HEAD preserves length with empty body')
            code,headers,data=request(url,headers={'Range':f'bytes={len(PAYLOAD)+1}-'});require(code==416 and data==b'' and headers.get('Content-Range')==f'bytes */{len(PAYLOAD)}','range 416 sanitized');passed('Range 416 suppresses upstream error body')
            for path in ('/v1/album','/v1/albums?offset=-1','/v1/search?size=101'):
                json_request(path,status=400)
            json_request('/v1/prepare',status=400,method='POST',body={'id':TRACK,'password':'unused'})
            json_request('/v1/prepare',status=400,method='POST',body=b'{"id":"x"} {}')
            json_request('/stream/not-a-capability',status=404)
            json_request('/v1/artists',status=403,headers={'Host':'remote.example:17831'})
            json_request('/v1/artists',status=403,headers={'Origin':'https://external.example'})
            passed('malformed input, unknown capability and origin/Host rejection')
            json_request('/v1/album?id=malformed',status=502)
            error=json_request('/v1/album?id=auth-error',status=401);require('40' in json.dumps(error),'Subsonic numeric code')
            json_request('/v1/album?id=http-error',status=502)
            error_url=json_request('/v1/prepare',method='POST',body={'id':'audio-error'})['url']
            json_request(error_url,status=502)
            passed('malformed upstream, authentication, HTTP and audio-error documents fail closed')
        finally:stop(process,log)
        untrusted=dict(config);del untrusted['ca_file']
        process,log=start(untrusted,directory)
        try:json_request('/v1/artists',status=502);passed('self-signed upstream rejected without ca_file')
        finally:stop(process,log)
        wrong_host=dict(config);wrong_host['server_url']=config['server_url'].replace('127.0.0.1','localhost')
        process,log=start(wrong_host,directory)
        try:json_request('/v1/artists',status=502);passed('hostname mismatch rejected even with trusted ca_file')
        finally:stop(process,log)
        fixture.shutdown();fixture.server_close()
    require(not fixture_errors,'fixture received valid protocol/auth/identifiers on every successful TLS request')
    report=dict(passed=True,unadjusted_rejection=dict(exit_status=failed.returncode,stderr=failed.stderr.strip()),adjusted_usage_exit_status=usage.returncode,qemu=subprocess.check_output(['qemu-mipsel','--version'],text=True).splitlines()[0],cpu='P5600',kernel_emulation='Linux user-mode syscall translation, not the supplied device kernel',adjustment=adjustment,
                checks=checks,upstream_endpoint_counts=dict(requests),unique_auth_salts=len(salts),fixture_errors=fixture_errors,stream_payload_size=len(PAYLOAD),stream_payload_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                credentials='Generated disposable fixture-only credentials; never printed or saved in report',tls='Self-signed one-day RSA certificate with IP SAN127.0.0.1; ca_file trust succeeds, missing trust and wrong host both fail',
                limitations=['No physical M1 Plus or XBurst2 CPU execution','No device kernel NaN-policy proof','No valid audio decoding or DAC playback; byte transport fixture only','No Wi-Fi stability, device CA/time, RAM or flash capacity verification','No live Navidrome server credentials used'])
    RESULT.write_text(json.dumps(report,indent=2)+'\n')
    print('RESULT '+str(RESULT),flush=True)

if __name__=='__main__':main()
