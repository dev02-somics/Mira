"""Exercise the real player UI with local, simulated API responses only."""
import base64,json,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wifi_browser_check import CDP,CHROME

profile=tempfile.mkdtemp(prefix='mira-seek-browser-')
proc=subprocess.Popen([CHROME,'--headless=new','--disable-gpu','--no-first-run',
                       '--remote-debugging-port=9231','--user-data-dir='+profile,'about:blank'],
                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 for attempt in range(40):
  try:
   pages=json.load(urlopen('http://127.0.0.1:9231/json',timeout=1));break
  except OSError:time.sleep(.25)
 cdp=CDP(next(p['webSocketDebuggerUrl'] for p in pages if p['type']=='page'))
 cdp.call('Page.enable')
 cdp.call('Emulation.setDeviceMetricsOverride',width=430,height=900,deviceScaleFactor=1,mobile=True)
 cdp.call('Page.addScriptToEvaluateOnNewDocument',source='''
window.__commands=[];
const fileId='11111111-1111-4111-8111-111111111111';
window.__playback={fileId,fileName:'filetestdatka.mp3',durationSeconds:156,positionSeconds:30,
 positionUpdatedAt:new Date().toISOString(),state:'PAUSED',repeat:'one',volume:35};
const nativeFetch=window.fetch;
window.fetch=async (url,options={})=>{
 let data;
 if(String(url).startsWith('/api/broker-status'))data={connected:true};
 else if(String(url)==='/api/iot/devices')data={ok:true,devices:[{deviceId:'SOMICS-000001',
 activatedAt:new Date().toISOString(),online:true,lastSeenAt:new Date().toISOString(),playback:window.__playback}]};
 else if(String(url).endsWith('/control')){
 const cmd=JSON.parse(options.body);window.__commands.push(cmd);
 if(cmd.action==='seek')window.__playback={...window.__playback,positionSeconds:cmd.positionSeconds,positionUpdatedAt:new Date().toISOString()};
 data={ok:true,id:'test-command'};
 }else return nativeFetch(url,options);
 return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
};
''')
 cdp.call('Page.navigate',url='http://127.0.0.1:3030')
 cdp.wait("!!document.querySelector('#target-device option[value=\"SOMICS-000001\"]')")
 cdp.evaluate("const select=document.getElementById('target-device'); select.value='SOMICS-000001';select.dispatchEvent(new Event('change',{bubbles:true}));")
 cdp.wait("!!document.querySelector('button[aria-label=\"Tua lùi 10 giây\"]')")
 cdp.evaluate("document.querySelector('button[aria-label=\"Tua lùi 10 giây\"]').click()")
 cdp.wait("window.__commands.length===1")
 assert cdp.evaluate('window.__commands[0].positionSeconds')==20
 cdp.wait("!document.querySelector('button[aria-label=\"Tua tới 10 giây\"]').disabled")
 cdp.evaluate("document.querySelector('button[aria-label=\"Tua tới 10 giây\"]').click()")
 cdp.wait("window.__commands.length===2")
 assert cdp.evaluate('window.__commands[1].positionSeconds')==30
 cdp.wait("!document.querySelector('input[id^=seek-]').disabled")
 cdp.evaluate("document.querySelector('input[id^=seek-]').scrollIntoView({block:'center'})")
 box=cdp.evaluate("(()=>{const r=document.querySelector('input[id^=seek-]').getBoundingClientRect();return {x:r.x+r.width*.75,y:r.y+r.height/2}})()")
 cdp.call('Input.dispatchMouseEvent',type='mousePressed',x=box['x'],y=box['y'],button='left',clickCount=1)
 cdp.call('Input.dispatchMouseEvent',type='mouseReleased',x=box['x'],y=box['y'],button='left',clickCount=1)
 cdp.wait('window.__commands.length===3')
 target=cdp.evaluate('window.__commands[2].positionSeconds')
 assert 110<target<125,target
 assert cdp.evaluate("window.__playback.state==='PAUSED'")
 assert cdp.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 cdp.evaluate("document.querySelector('section[aria-label^=\"Điều khiển phát nhạc\"]').scrollIntoView({block:'start'})")
 image=cdp.call('Page.captureScreenshot',format='png')['data']
 Path('diagnostics/seek-controls-mobile.png').write_bytes(base64.b64decode(image))
 print('UI PASS: back/forward 10s, pointer seek, paused state and mobile width')
 print('Seek commands:',cdp.evaluate('window.__commands'))
finally:
 proc.terminate()
 proc.wait(timeout=10)
