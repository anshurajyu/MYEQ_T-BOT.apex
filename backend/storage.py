import json
import sqlite3
import time
import threading
from pathlib import Path

class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True,exist_ok=True)
        self.root=root
        self.lock=threading.RLock()
        self.db=sqlite3.connect(root/'tbot.sqlite3',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, body TEXT, updated REAL, PRIMARY KEY(kind,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, timestamp REAL, body TEXT)')
        self.db.commit()
    def put(self,kind,key,value):
        with self.lock:self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(kind,key,json.dumps(value,allow_nan=False),time.time()));self.db.commit()
    def get(self,kind,key,default=None):
        with self.lock:row=self.db.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,key)).fetchone()
        return json.loads(row[0]) if row else default
    def list(self,kind):
        with self.lock:return [{'id':r[0],**json.loads(r[1])} for r in self.db.execute('SELECT id,body FROM records WHERE kind=? ORDER BY updated DESC',(kind,))]
    def log(self,event):
        with self.lock:
            self.db.execute('INSERT INTO events(timestamp,body) VALUES(?,?)',(time.time(),json.dumps(event)))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)');self.db.commit()
    def events(self):
        with self.lock:return [{'timestamp':r[0],**json.loads(r[1])} for r in self.db.execute('SELECT timestamp,body FROM events ORDER BY id DESC LIMIT 60')]

    def export_map(self,name,grid):
        import hashlib
        folder=self.root/'maps'/hashlib.sha256(name.encode()).hexdigest()[:16]
        folder.mkdir(parents=True,exist_ok=True)
        w,h=grid['width'],grid['height'];data=grid['data']
        pixels=bytes(205 if data[y*w+x]<0 else 0 if data[y*w+x]>50 else 254 for y in range(h-1,-1,-1) for x in range(w))
        (folder/'map.pgm').write_bytes(f'P5\n{w} {h}\n255\n'.encode()+pixels)
        o=grid['origin'];spec={'image':'map.pgm','mode':'trinary','resolution':grid['resolution'],'origin':[o['x'],o['y'],o.get('yaw',0)],'negate':0,'occupied_thresh':0.65,'free_thresh':0.196}
        (folder/'map.yaml').write_text(json.dumps(spec))
        return str((folder/'map.yaml').resolve())
