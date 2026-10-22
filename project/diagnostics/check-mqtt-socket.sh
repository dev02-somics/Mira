python3 -B -u - <<'PY'
import inspect,socket,time
import paho.mqtt.client as mqtt
print(inspect.getsource(mqtt.Client._create_socket_connection))
print(inspect.getsource(mqtt.Client._create_socketpair) if hasattr(mqtt.Client,'_create_socketpair') else '')
print('CONNECT_TIMEOUT_DEFAULT',getattr(mqtt.Client(), '_connect_timeout',None))
start=time.monotonic()
try:
 with socket.create_connection(('45.252.248.184',8083),timeout=5):print('BROKER_TCP_OK',time.monotonic()-start)
except OSError as e:print('BROKER_TCP_ERROR',type(e).__name__)
PY
cat /tmp/mira-player-status.json
ip route
