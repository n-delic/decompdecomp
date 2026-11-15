"""Bounded cache of validated saved files; no source is written to another location."""
import threading
from collections import OrderedDict


class FileCache:
    def __init__(self, limit=8*1024*1024):
        self.lock=threading.RLock()
        self.limit=limit
        self.bytes=0
        self.files=OrderedDict()

    @staticmethod
    def key(path, info):
        return (str(path.absolute()),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_mode)

    def scan(self):
        return _Scan(self)

    def get(self, path, info):
        with self.lock:return self._get(path,info)

    def _get(self, path, info, protected=None):
        key=self.key(path,info)
        value=self.files.get(key)
        if value is not None:
            self.files.move_to_end(key)
            if protected is not None:protected.add(key)
        return value

    def put(self, path, info, text):
        with self.lock:self._put(path,info,text)

    def _put(self, path, info, text, protected=None):
        size=len(text.encode('utf-8'))
        if size>self.limit:return
        key=self.key(path,info)
        if protected is not None:
            # A sequential scan larger than the cache would otherwise evict
            # every warm file before reaching it on the next pass. Keep this
            # scan's hits/admissions; another scan can replace unused entries.
            old=self.files.get(key)
            needed=self.bytes-(len(old.encode('utf-8')) if old is not None else 0)+size-self.limit
            if needed>0 and len(protected)>=len(self.files):return
            evict=[]
            for candidate,value in self.files.items():
                if needed<=0:break
                if candidate!=key and candidate not in protected:
                    evict.append(candidate)
                    needed-=len(value.encode('utf-8'))
            if needed>0:return
            for candidate in evict:
                self.bytes-=len(self.files.pop(candidate).encode('utf-8'))
        old=self.files.pop(key,None)
        if old is not None:self.bytes-=len(old.encode('utf-8'))
        self.files[key]=text
        self.bytes+=size
        if protected is not None:protected.add(key)
        while self.bytes>self.limit:
            _,old=self.files.popitem(last=False)
            self.bytes-=len(old.encode('utf-8'))


class _Scan:
    """Per-read admission policy; cache contents and byte limit remain shared."""
    def __init__(self, cache):
        self.cache=cache
        self.protected=set()

    def get(self, path, info):
        with self.cache.lock:return self.cache._get(path,info,self.protected)

    def put(self, path, info, text):
        with self.cache.lock:self.cache._put(path,info,text,self.protected)
