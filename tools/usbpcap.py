"""Minimal pcapng + USBPcap parser. yields dicts per packet."""
import struct
def packets(path):
    d=open(path,'rb').read(); off=0; endian='<'; tsres={}
    while off < len(d):
        btype,blen=struct.unpack_from('<II',d,off)
        body=d[off+8:off+blen-4]
        if btype==6:  # EPB
            ifid,tsh,tsl,caplen,origlen=struct.unpack_from('<IIIII',body)
            pkt=body[20:20+caplen]; ts=((tsh<<32)|tsl)/1e6
            yield ts, pkt
        off+=blen
def parse(pkt):
    hl,irp,status,func,info,bus,dev,ep,xfer,dlen=struct.unpack_from('<HQIHBHHBBI',pkt)
    r=dict(hl=hl,irp=irp,status=status,func=func,dir='in' if info&1 else 'out',bus=bus,dev=dev,ep=ep,xfer=xfer,dlen=dlen)
    if xfer==2: r['stage']=pkt[27]
    r['data']=pkt[hl:]
    return r
