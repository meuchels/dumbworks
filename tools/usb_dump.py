import os
import struct
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usbpcap import packets,parse
DEV=int(sys.argv[2])
t0=None; pending={}
for ts,p in packets(sys.argv[1]):
    r=parse(p)
    if r['dev']!=DEV: continue
    t0=t0 or ts; t=ts-t0; dat=r['data']
    if r['xfer']==2:
        st=r['stage']
        if st==0:  # setup
            bm,req,val,idx,ln=struct.unpack('<BBHHH',dat[:8]) if len(dat)>=8 else (0,0,0,0,0)
            extra=dat[8:]
            print(f"{t:8.3f} CTRL setup bm={bm:02x} req={req:02x} val={val:04x} idx={idx:04x} len={ln} {extra[:64].hex()}{'..' if len(extra)>64 else ''}")
        elif st==1:
            print(f"{t:8.3f}      data {r['dir']} {len(dat)}B {dat[:64].hex()}{'..' if len(dat)>64 else ''}")
        elif st==2:
            print(f"{t:8.3f}      status {r['dir']} st={r['status']:x} {len(dat)}B {dat[:64].hex()}")
        elif st==3:
            print(f"{t:8.3f}      complete st={r['status']:x} {len(dat)}B {dat[:64].hex()}{'..' if len(dat)>64 else ''}")
    else:
        if r['dir']=='out' and r['ep']&0x80 and not dat: continue  # bulk in request submit
        print(f"{t:8.3f} BULK ep={r['ep']:02x} {r['dir']} st={r['status']:x} len={r['dlen']} {dat[:32].hex()}{'..' if len(dat)>32 else ''}")
