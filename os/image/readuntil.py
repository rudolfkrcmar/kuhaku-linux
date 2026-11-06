import socket,time,re,sys
s=socket.socket(socket.AF_UNIX); s.connect('/tmp/kvm/serial.sock'); s.settimeout(1)
out=''; end=time.time()+float(sys.argv[2])
while time.time()<end:
    try:
        d=s.recv(65536)
        if d: out+=d.decode(errors='replace')
    except socket.timeout: pass
    if sys.argv[1] in out: break
print(re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]','',out)[-1500:])
