"""Pinned, resumable multi-origin downloads. Network probes never install code."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener


class InstallCancelled(RuntimeError):pass


class DownloadUnavailable(RuntimeError):pass


@lru_cache(maxsize=128)
def _digest(path,size,mtime):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(2*1024**2),b''):digest.update(chunk)
    return digest.hexdigest()


def verified(path,artifact):
    try:
        info=Path(path).stat()
        return info.st_size==artifact['sizeBytes'] and _digest(str(path),info.st_size,info.st_mtime_ns)==artifact['sha256']
    except OSError:return False


class Downloader:
    def __init__(self,cache,*,mode='auto',opener=None,timeout=12,allow_http=False,event=None,source_filter=None):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.mode=mode;self.opener=opener or build_opener();self.timeout=timeout;self.allow_http=allow_http
        self.event=event or (lambda **values:None);self.source_filter=source_filter

    def path(self,artifact):
        name=artifact['filename']
        if name!=Path(name).name or not re.fullmatch(r'[a-f0-9]{64}',artifact['sha256']):raise ValueError('invalid pinned artifact identity')
        return self.cache/(artifact['sha256']+'-'+name)

    def _request(self,url,start=None,end=None):
        if urlsplit(url).scheme!='https' and not self.allow_http:raise ValueError('download sources must use HTTPS')
        headers={'User-Agent':'CutVoke optional extension installer','Accept-Encoding':'identity'}
        if start is not None:headers['Range']=f'bytes={start}-'+('' if end is None else str(end))
        return Request(url,headers=headers)

    def _check(self,response,artifact,start):
        if urlsplit(response.url).scheme!='https' and not self.allow_http:raise ValueError('insecure download redirect')
        if 'text/html' in response.headers.get('Content-Type','').lower():raise ValueError('source returned a web page instead of the pinned file')
        value=response.headers.get('Content-Range','')
        if response.status==206:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',value)
            if not match or int(match[1])!=start or int(match[3])!=artifact['sizeBytes']:raise ValueError('source returned a mismatched byte range')
        elif response.status!=200 or start:raise ValueError('source does not support this resume position')
        elif response.headers.get('Content-Length') and int(response.headers['Content-Length'])!=artifact['sizeBytes']:raise ValueError('source file size does not match release catalog')

    def probe(self,source,artifact):
        started=time.monotonic()
        try:
            with self.opener.open(self._request(source['url'],0,0),timeout=min(self.timeout,5)) as response:
                self._check(response,artifact,0);response.read(1)
            return {**source,'available':True,'seconds':time.monotonic()-started}
        except Exception as error:return {**source,'available':False,'diagnostic':str(error)[:300]}

    def sources(self,artifact):
        values=[value for value in artifact.get('sources',[]) if self.source_filter is None or self.source_filter(value)]
        if self.mode=='offline':return []
        # Two small probes at a time keep first-use feedback bounded.
        with ThreadPoolExecutor(max_workers=2) as pool:probes=list(pool.map(lambda value:self.probe(value,artifact),values))
        preference={'china':'cn','global':'global'}.get(self.mode)
        return sorted(probes,key=lambda value:(not value['available'],bool(preference and value.get('region')!=preference),value.get('seconds',999)))

    def fetch(self,artifact,*,cancelled=lambda:False,bundled_root=None):
        target=self.path(artifact)
        if cancelled():raise InstallCancelled('installation cancelled')
        if verified(target,artifact):return target
        if artifact.get('bundled'):
            source=(Path(bundled_root)/artifact['bundled']).resolve()
            if not source.is_relative_to(Path(bundled_root).resolve()) or not verified(source,artifact):raise ValueError('bundled runtime package failed release verification')
            temporary=target.with_suffix(target.suffix+'.part');shutil.copyfile(source,temporary);os.replace(temporary,target);return target
        partial=target.with_suffix(target.suffix+'.part');attempts=[]
        sources=self.sources(artifact)
        for source in sources:
            if cancelled():raise InstallCancelled('installation cancelled')
            if not source['available']:
                attempts.append({'source':source.get('label',urlsplit(source['url']).hostname),'error':source['diagnostic']});continue
            self.event(phase='downloading',source=source.get('label',urlsplit(source['url']).hostname),filename=artifact['filename'],attempts=attempts)
            for retry in range(2):
                if cancelled():raise InstallCancelled('installation cancelled')
                start=partial.stat().st_size if partial.exists() else 0
                if start>=artifact['sizeBytes']:
                    if verified(partial,artifact):os.replace(partial,target);return target
                    partial.unlink(missing_ok=True);start=0
                try:
                    with self.opener.open(self._request(source['url'],start if start else None),timeout=self.timeout) as response:
                        self._check(response,artifact,start)
                        completed=start;last=0
                        with partial.open('ab' if start else 'wb') as stream:
                            while True:
                                if cancelled():raise InstallCancelled('installation cancelled')
                                # read1 returns available TLS data instead of
                                # waiting to fill a large buffer on a slow origin.
                                # This keeps progress/cancellation responsive.
                                data=getattr(response,'read1',response.read)(512*1024)
                                if not data:break
                                completed+=len(data)
                                if completed>artifact['sizeBytes']:raise ValueError('source exceeded the pinned file size')
                                stream.write(data)
                                if time.monotonic()-last>.2:
                                    self.event(phase='downloading',filename=artifact['filename'],fileBytes=completed,fileTotal=artifact['sizeBytes']);last=time.monotonic()
                            stream.flush();os.fsync(stream.fileno())
                    if not verified(partial,artifact):
                        if partial.stat().st_size==artifact['sizeBytes']:partial.unlink()
                        raise ValueError('download interrupted or SHA-256 mismatch')
                    os.replace(partial,target);return target
                except InstallCancelled:raise
                except Exception as error:
                    attempts.append({'source':source.get('label',urlsplit(source['url']).hostname),'error':str(error)[:300]})
                    # A server may ignore Range. Restart this file once rather
                    # than appending a full response to an existing prefix.
                    if start and any(text in str(error) for text in ('resume position','mismatched byte range')):partial.unlink(missing_ok=True)
                    self.event(phase='switching_source',attempts=attempts)
        raise DownloadUnavailable('No verified download source completed '+artifact['filename']+'; '+str(attempts)[-1600:])
