import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest


def test_engine_selection_keeps_explicit_qt_and_headless_fallback():
    from chrome_provider import find_chrome_binary
    which = lambda name: '/usr/bin/google-chrome'
    assert find_chrome_binary('qt', environ={'DISPLAY': ':1'}, platform='linux', which=which) is None
    assert find_chrome_binary('auto', environ={}, platform='linux', which=which) is None
    assert find_chrome_binary('auto', environ={'DISPLAY': ':1', 'QT_QPA_PLATFORM':'offscreen'}, platform='linux', which=which) is None
    assert find_chrome_binary('auto', environ={'DISPLAY': ':1'}, platform='linux', which=which) == '/usr/bin/google-chrome'
    with pytest.raises(RuntimeError):
        find_chrome_binary('chrome', environ={}, platform='linux', which=which)


@pytest.mark.parametrize('url', ['http://127.0.0.1:8123/', 'http://127.1/', 'http://2130706433/',
    'http://10.0.0.1/json/state', 'file:///etc/passwd', 'https://localhost/', 'http://[::1]/'])
def test_remote_requests_cannot_reach_local_devices_or_backend(url):
    from chrome_provider import request_allowed
    assert not request_allowed('youtube_music',url,main_frame=False)


def test_request_policy_allows_provider_auth_but_not_foreign_main_frame():
    from chrome_provider import request_allowed
    assert request_allowed('youtube_music','https://accounts.google.com/signin',main_frame=True)
    assert request_allowed('apple_music','https://idmsa.apple.com/appleauth/auth',main_frame=True)
    assert request_allowed('apple_music','https://auth.music.apple.com/auth',main_frame=False)
    assert not request_allowed('youtube_music','https://example.com/',main_frame=True)
    assert request_allowed('youtube_music','https://fonts.gstatic.com/font',main_frame=False)
    assert request_allowed('youtube_music','about:blank',main_frame=True)


def test_observer_guard_never_executes_inside_provider_login_page():
    import json,subprocess,shutil
    if not shutil.which('node'):pytest.skip('node unavailable')
    from chrome_provider import guarded_script
    expression=guarded_script('youtube_music','(() => { globalThis.ran=true; return 123; })()')
    result=subprocess.run(['node','-e',f'global.location={{origin:"https://accounts.google.com"}}; console.log(JSON.stringify({{value:eval({json.dumps(expression)}),ran:!!global.ran}}))'],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)=={'value':None,'ran':False}
    result=subprocess.run(['node','-e',f'global.location={{origin:"https://music.youtube.com"}}; console.log(JSON.stringify(eval({json.dumps(expression)})))'],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==123


def test_full_worker_queue_completes_rejected_observer_instead_of_hanging(tmp_path):
    from chrome_provider import BrowserWorker
    outcomes=[]
    worker=BrowserWorker('youtube_music','chrome',tmp_path,lambda cb,result:cb(result) if cb else None,lambda pid:None,lambda text:None,max_pending=1)
    assert worker.submit('eval','1',None)
    assert not worker.submit('eval','2',outcomes.append)
    assert outcomes==[None]
    worker.close()
    assert not worker.submit('eval','3',outcomes.append)
    assert outcomes==[None,None]


def test_fetch_events_fail_private_requests_and_continue_public_requests(tmp_path):
    from chrome_provider import BrowserWorker
    sent=[]
    worker=BrowserWorker('youtube_music','chrome',tmp_path,lambda *_:None,lambda *_:None,lambda *_:None)
    worker.transport=type('Transport',(),{'send':lambda _,method,params=None,session_id=None:sent.append((method,params,session_id))})()
    worker.frames['s']='main'
    for url in ['http://127.0.0.1:8123/', 'https://music.youtube.com/']:
        worker.on_event({'method':'Fetch.requestPaused','sessionId':'s','params':{'requestId':'r','frameId':'main','resourceType':'Document','request':{'url':url}}})
    assert sent[0]==('Fetch.failRequest',{'requestId':'r','errorReason':'BlockedByClient'},'s')
    assert sent[1]==('Fetch.continueRequest',{'requestId':'r'},'s')


def test_desktop_entry_accepts_explicit_engine_choice(monkeypatch):
    import light_desktop
    calls=[]
    monkeypatch.setattr(light_desktop,'run_desktop',lambda **kwargs:calls.append(kwargs) or 0)
    assert light_desktop.main(['--provider-engine','qt'])==0
    assert calls[-1]['provider_engine']=='qt'
    assert light_desktop.main(['--provider-engine','chrome'])==0
    assert calls[-1]['provider_engine']=='chrome'


def test_navigation_context_error_does_not_kill_player_or_lose_next_observation(tmp_path,monkeypatch):
    from chrome_provider import BrowserWorker
    import chrome_transport
    seen=[];errors=[]
    class Transport:
        process=type('Process',(),{'pid':123})()
        on_event=None
        def __init__(self,*_,**_kwargs):self.evaluations=0
        def send(self,*a,**kw):pass
        def call(self,method,params=None,**kw):
            if method=='Target.getTargets':return {'targetInfos':[{'type':'page','targetId':'main'}]}
            if method=='Target.attachToTarget':return {'sessionId':'session'}
            if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'frame'}}}
            if method=='Runtime.evaluate':
                self.evaluations+=1
                if self.evaluations==1:raise RuntimeError('Chrome command failed')
                return {'result':{'value':42}}
            return {}
        def close(self):pass
    monkeypatch.setattr(chrome_transport,'ChromeTransport',Transport)
    def finish(cb,value):
        cb(value)
        if len(seen)==2:worker.stop.set()
    worker=BrowserWorker('youtube_music','chrome',tmp_path,finish,lambda _:None,errors.append,window_finder=lambda _:42)
    worker.submit('eval','1',seen.append);worker.submit('eval','2',seen.append)
    worker.run()
    assert seen==[None,42]
    assert errors==[]


def test_main_document_and_spa_navigation_advance_epoch_but_iframes_do_not(tmp_path):
    from chrome_provider import BrowserWorker
    seen=[]
    worker=BrowserWorker('youtube_music','chrome',tmp_path,lambda *_:None,lambda _:None,lambda _:None,navigation=seen.append)
    worker.session='s';worker.frames['s']='main'
    worker.on_event({'method':'Page.frameNavigated','sessionId':'s','params':{'frame':{'id':'child','parentId':'main'}}})
    assert worker.epoch==0
    worker.on_event({'method':'Page.frameNavigated','sessionId':'s','params':{'frame':{'id':'main'}}})
    worker.on_event({'method':'Page.navigatedWithinDocument','sessionId':'s','params':{'frameId':'main'}})
    assert worker.epoch==2 and seen==[1,2]


def test_qt_delivery_discards_callback_after_navigation_and_shutdown_is_idempotent(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread
    import chrome_provider
    app=QApplication.instance() or QApplication([])
    class Worker:
        def __init__(self,*args,**kw):
            self.complete=args[3];self.epoch=0;self.closed=0
        def start(self):pass
        def close(self):self.closed+=1
        def submit(self,*args):return True
    monkeypatch.setattr(chrome_provider,'BrowserWorker',Worker)
    view=chrome_provider.create_provider_view('youtube_music','chrome',tmp_path)
    seen=[]
    def result(value):
        assert QThread.currentThread()==app.thread()
        seen.append(value)
    view.worker.complete(result,{'title':'old track'})
    view.worker.epoch=1
    app.processEvents()
    assert seen==[None]
    view.worker.complete(result,{'title':'new track'})
    app.processEvents()
    assert seen[-1]=={'title':'new track'}
    view.shutdown();view.shutdown()
    assert view.worker.closed==1
    view.worker.complete(result,{'title':'late'})
    app.processEvents()
    assert len(seen)==2
    view.deleteLater();app.processEvents()


def test_selected_page_detach_fails_visibly_instead_of_silent_dead_session(tmp_path):
    from chrome_provider import BrowserWorker
    worker=BrowserWorker('youtube_music','chrome',tmp_path,lambda *_:None,lambda _:None,lambda _:None)
    worker.session='player';worker.frames['player']='root'
    worker.on_event({'method':'Target.detachedFromTarget','params':{'sessionId':'popup'}})
    assert worker.session=='player'
    with pytest.raises(ConnectionError):
        worker.on_event({'method':'Target.detachedFromTarget','params':{'sessionId':'player'}})
    assert worker.session is None
