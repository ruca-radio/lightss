"""One owner for controller-microphone music shows and steady TV lighting."""
from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import fcntl
import ipaddress
import math
import os
import socket
import threading
import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import activity_intelligence
import color_lab
import lightctl

DEFAULTS = dict(enabled=False, mode='auto', tv_theme='warm', music_brightness=.65,
                day_brightness=.30, evening_brightness=.15, night_brightness=.05,
                day_start=7, evening_start=18, night_start=22, timezone='America/Detroit',
                debounce_s=6.0, unknown_hold_s=12.0, silence_hold_s=20.0,
                music_motion='auto', music_speed=1.0, music_intensity=.85,
                music_colorfulness=.9)
THEMES = {'warm': [210, 145, 85], 'neutral': [190, 195, 210], 'blue': [80, 120, 210]}
CACHE_TTL_S = 300.0
CHAPTER_FIRST_S = 15.0
CHAPTER_INTERVAL_S = 120.0
CHAPTER_RETRY_S = 30.0
RENDERER_MAX_ATTEMPTS = 2
RENDERER_RETRY_DELAY_S = 2.0
_write_context = threading.local()


def validate_config(raw=None):
    if raw is not None and not isinstance(raw, dict):
        raise ValueError('Smart controller settings must be an object.')
    cfg = {**DEFAULTS, **(raw or {})}
    if not isinstance(cfg['enabled'], bool): raise ValueError('enabled must be a boolean.')
    if cfg['mode'] not in {'auto', 'music', 'tv', 'manual'}: raise ValueError('Invalid smart mode.')
    if cfg['tv_theme'] not in THEMES: raise ValueError('Invalid steady TV theme.')
    for key in ('music_brightness','day_brightness','evening_brightness','night_brightness'):
        value = cfg[key]
        maximum=1
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f'{key} must be a number between 0 and {maximum}.')
    if cfg['music_motion'] not in ('auto', 'flow', 'punch', 'chase', 'spectrum', 'comet', 'ripple'):
        raise ValueError('Invalid music motion.')
    for key, low, high in (('music_speed', .25, 4.0), ('music_intensity', 0, 1),
                           ('music_colorfulness', 0, 1)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be a number between {low} and {high}.')
    for key in ('day_start','evening_start','night_start'):
        if type(cfg[key]) is not int or not 0 <= cfg[key] <= 23: raise ValueError(f'{key} must be an hour 0–23.')
    if not cfg['day_start'] < cfg['evening_start'] < cfg['night_start']:
        raise ValueError('Day, evening and night hours must be in increasing order.')
    try: ZoneInfo(cfg['timezone'])
    except (ZoneInfoNotFoundError, TypeError, ValueError): raise ValueError('Invalid schedule timezone.') from None
    for key in ('debounce_s','unknown_hold_s','silence_hold_s'):
        value=cfg[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= 120:
            raise ValueError(f'Invalid {key}.')
    return cfg


def tv_brightness(cfg, when):
    if when.tzinfo is not None: when=when.astimezone(ZoneInfo(cfg['timezone']))
    hour=when.hour
    if cfg['day_start'] <= hour < cfg['evening_start']: return cfg['day_brightness']
    if cfg['evening_start'] <= hour < cfg['night_start']: return cfg['evening_brightness']
    return cfg['night_brightness']


def observation_key(obs):
    media=obs.get('media_session') or {}
    return (obs.get('connected'),obs.get('awake'),obs.get('activity_hint'),obs.get('foreground_app'),
            media.get('package'),media.get('active'),media.get('state'),media.get('description'))


@contextmanager
def owned_write():
    previous=getattr(_write_context,'owned',False);_write_context.owned=True
    try: yield
    finally: _write_context.owned=previous


class SmartDirector(threading.Thread):
    def __init__(self, fleet, config=None, *, listener=None, renderer_factory=None,
                 observe_fn=None, classify_fn=None, ai_settings=None, clock=time.monotonic,
                 wall_clock=None, persist_manual=False, chapter_fn=None):
        super().__init__(name='lightss-smart-director',daemon=True)
        self.fleet=fleet;self.config=validate_config(config);self.clock=clock
        self.wall_clock=wall_clock or (lambda: datetime.now(ZoneInfo(self.config['timezone'])))
        self.listener=listener;self.renderer_factory=renderer_factory
        self.observe_fn=observe_fn;self.classify_fn=classify_fn;self.ai_settings=ai_settings or {}
        self.persist_manual=persist_manual;self.renderer=None
        self._lock=threading.RLock();self._stop_event=threading.Event()
        self._startup_event=threading.Event();self._startup_error=None
        self._mode='manual' if self.config['mode']=='manual' else 'starting'
        self._reason='Starting';self._error=None;self._applied_tv=None;self._brightness=0.0
        self._pending_mode=None;self._pending_since=clock();self._quiet_since=None
        self._song_show={}
        self._show_overrides={}
        self._look_dirty=False
        self._last_music_at=None;self._last_recipe=None;self._beat_times=deque(maxlen=30);self._last_seq=None
        self._observation={};self._decision={};self._observed_at=0.0
        self._observer=None;self._executor=None;self._future=None;self._future_key=None
        self._observer_started=False
        self._listener_stopped=False
        self._cache=OrderedDict()
        self._renderer_attempts=0;self._renderer_retry_at=0.0;self._renderer_fault=None
        self.chapter_fn=chapter_fn
        self._chapter_executor=None;self._chapter_future=None
        self._chapter_request_key=None;self._chapter_request_generation=None
        self._chapter_key=None;self._chapter_generation=0;self._chapter_due_at=None
        self._chapter_look={};self._chapter_count=0;self._chapter_error=None
        self._chapter_history=deque(maxlen=5)
        self._output_engine='ddp';self._native_effect=None;self._native_look={}
        self._audio_history=deque(maxlen=180);self._chapter_beats=deque(maxlen=256)

    def configure(self, updates):
        with self._lock:
            self.config=validate_config({**self.config,**updates})
            for key in updates:
                if key.startswith('music_'):
                    self._show_overrides.pop(key.removeprefix('music_'), None)
            if 'mode' in updates:
                self._renderer_attempts=0;self._renderer_retry_at=0.0;self._renderer_fault=None
                self._error=None
            if self.config['mode']=='manual': self._manual_locked()
            self._pending_mode=None

    def _make_listener(self):
        import wled_audio
        controllers=getattr(self.fleet,'controllers',[])
        source=(urlparse(controllers[0].host).hostname if controllers else None)
        if not source: raise ValueError('Controller microphone requires configured fleet host.')
        source=socket.gethostbyname(source)
        ipaddress.IPv4Address(source)
        route=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try: route.connect((source,11988));interface=route.getsockname()[0]
        finally: route.close()
        return wled_audio.WledAudioListener(interface_ip=interface,allowed_source=source)

    def _classify(self, observation):
        if self.classify_fn: return self.classify_fn(observation)
        import activity_intelligence
        return activity_intelligence.classify_context(observation,self.ai_settings)

    def _observe(self):
        if self.observe_fn: return self.observe_fn()
        import firetv
        return firetv.observe()

    def _observer_loop(self):
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='smart-music-recipe')
        try:
            while not self._stop_event.is_set():
                try: obs=self._observe()
                except Exception as exc: obs={'connected':False,'error':str(exc)}
                key=observation_key(obs)
                with self._lock:
                    self._observation=obs;self._observed_at=self.clock()
                    if self._future is not None and self._future.done():
                        try: result=self._future.result()
                        except Exception: result={'kind':'unknown','reason':'Classification unavailable.'}
                        self._cache[self._future_key]=(self.clock(),result)
                        while len(self._cache)>64:self._cache.popitem(last=False)
                        self._future=None
                    self._decision=self._cached_decision(key,self.clock()) or {}
                    if key not in self._cache and self._future is None and not self._stop_event.is_set():
                        self._future_key=key;self._future=self._executor.submit(self._classify,dict(obs))
                if self._stop_event.wait(3.0): break
        finally:
            self._executor.shutdown(wait=False,cancel_futures=True)

    def _cached_decision(self, key, now):
        cached=self._cache.get(key)
        if cached is None:return None
        stored_at,result=cached
        if now-stored_at>CACHE_TTL_S:
            self._cache.pop(key,None);return None
        self._cache.move_to_end(key)
        return result

    def _stop_renderer(self, *, reset_chapter=True):
        if reset_chapter:
            self._chapter_generation+=1
            self._chapter_key=None;self._chapter_due_at=None;self._chapter_look={}
        if self.renderer is not None:
            renderer=self.renderer
            was_alive=renderer.is_alive()
            renderer.stop()
            if was_alive:renderer.join(timeout=3)
            elif getattr(renderer,'ident',None) is None:
                try:renderer.transport.close()
                except (AttributeError,OSError):pass
            if renderer.is_alive(): raise RuntimeError('Renderer did not stop; refusing another output owner.')
            self.renderer=None
        self._last_recipe=None

    def _stop_listener(self):
        listener=self.listener
        if listener is not None and not self._listener_stopped:
            self._listener_stopped=True
            listener.stop()

    def _stop_observer(self):
        observer=self._observer
        if observer is not None and self._observer_started and observer is not threading.current_thread():
            observer.join(timeout=4)
            if observer.is_alive():
                self._error='TV observer did not stop cleanly.'
            else:
                self._observer=None;self._observer_started=False

    def _cleanup_components(self):
        try:
            with self._lock:self._stop_renderer()
        except Exception as exc:
            with self._lock:self._error=str(exc)
        try:self._stop_listener()
        except Exception as exc:
            with self._lock:self._error=str(exc)
        self._stop_observer()

    def _post(self,payload):
        with owned_write(): result=self.fleet.post_state(payload)
        if isinstance(result,dict):
            failures=[str(v.get('error','controller write failed')) for v in result.values()
                      if isinstance(v,dict) and v.get('ok') is False]
            if failures: raise RuntimeError('; '.join(failures))

    def _tv(self, reason):
        renderer_was_healthy=bool(self.renderer and self.renderer.is_alive())
        self._stop_renderer()
        self._output_engine='ddp';self._native_effect=None;self._native_look={}
        if renderer_was_healthy:
            self._renderer_attempts=0;self._renderer_retry_at=0.0;self._renderer_fault=None
        brightness=tv_brightness(self.config,self.wall_clock())
        signature=(brightness,self.config['tv_theme'])
        if signature != self._applied_tv or self._mode != 'tv':
            transition=200 if self._mode=='tv' else 30
            self._post({'on':True,'bri':round(brightness*255),'transition':transition,
                        'seg':[{'fx':0,'frz':False,'on':True,'bri':255,
                                'col':[THEMES[self.config['tv_theme']]]}]})
            self._applied_tv=signature
        self._brightness=brightness;self._mode='tv';self._reason=reason

    def _music(self, decision, reason):
        if self._mode!='music':self._last_music_at=self.clock()
        if self._output_engine=='native':
            brightness=self.config['music_brightness']
            if brightness!=self._brightness:
                self._post({'bri':round(brightness*255)})
            self._brightness=brightness;self._mode='music';self._reason=reason
            return
        if self.renderer is None or not self.renderer.is_alive():
            renderer_failed=self.renderer is not None
            self._stop_renderer(reset_chapter=False)
            now=self.clock()
            if renderer_failed:
                self._renderer_attempts+=1
                self._renderer_retry_at=now+RENDERER_RETRY_DELAY_S
                self._renderer_fault='Music renderer stopped unexpectedly.'
            if self._renderer_attempts>=RENDERER_MAX_ATTEMPTS:
                self._renderer_fault='Music renderer failed after two attempts; using steady lighting.'
                self._tv(self._renderer_fault);return
            if self._renderer_attempts and now<self._renderer_retry_at:
                self._renderer_fault='Music renderer stopped; waiting to retry.'
                self._tv(self._renderer_fault);return
            self._post({'on':True,'bri':255,'transition':0,
                        'seg':[{'fx':0,'frz':False,'on':True,'bri':255,'col':[[0,0,0]]}]})
            factory=self.renderer_factory
            if factory is None:
                import realtime
                factory=realtime.AudioReactiveRunner
            try:
                self.renderer=factory(self.fleet,self.listener.get_snapshot,fps=30,
                                      brightness=self.config['music_brightness'],colors=decision.get('colors'))
                self.renderer.start()
                self._renderer_fault=None
            except Exception as exc:
                self._renderer_attempts+=1
                self._renderer_retry_at=now+RENDERER_RETRY_DELAY_S
                self._renderer_fault=f'Music renderer failed: {exc}'
                try:self._stop_renderer()
                finally:self._tv(self._renderer_fault)
                raise
        # Keep the song recipe across metadata gaps, but ALWAYS apply live user
        # controls. Previously an unknown result also blocked brightness updates.
        if decision.get('kind') == 'music' or self._last_recipe is None:
            self._song_show = activity_intelligence.normalize_show_recipe(decision.get('show'))
            colors = color_lab.normalize_palette(decision.get('colors'))
            composition = decision.get('composition_mode', 'center_vs_outer')
            if composition not in color_lab.COMPOSITION_MODES:
                composition = 'center_vs_outer'
        else:
            colors, composition = self._last_recipe[0], self._last_recipe[2]
        motion = self.config['music_motion']
        if motion == 'auto':
            motion = self._song_show.get('motion', 'auto')
        speed = min(4, max(.25, self.config['music_speed'] * self._song_show.get('speed', 1)))
        intensity = self.config['music_intensity'] * self._song_show.get('intensity', 1)
        look = dict(colors=[list(c) for c in colors], brightness=self.config['music_brightness'],
                    composition_mode=composition, motion=motion, speed=speed, intensity=intensity,
                    colorfulness=self.config['music_colorfulness'], band_gains=[1.0]*16)
        look.update(self._chapter_look)
        look.update(self._show_overrides)
        recipe = (tuple(tuple(c) for c in look['colors']), look['brightness'], look['composition_mode'],
                  look['motion'], look['speed'], look['intensity'], look['colorfulness'], tuple(look['band_gains']))
        if self._look_dirty or recipe != self._last_recipe:
            self.renderer.update_look(**look)
            self._last_recipe = recipe
            self._look_dirty = False
        self._brightness=look['brightness'];self._mode='music';self._reason=reason
        self._applied_tv=None

    def _apply_native_chapter(self, chapter):
        """Validate the live common catalog before revoking the DDP owner."""
        import music_chapters
        effect=chapter.get('effect')
        if type(effect) is not int or effect not in {item['id'] for item in music_chapters.available_native_effects(self.fleet)}:
            raise ValueError('Native effect is not available as a common 1D audio effect.')
        colors=chapter.get('colors')
        _,validated=_validate_show_request({'action':'tune','colors':colors})
        speed=chapter.get('native_speed',160);intensity=chapter.get('native_intensity',180)
        if type(speed) is not int or not 0<=speed<=255 or type(intensity) is not int or not 0<=intensity<=255:
            raise ValueError('Native speed and intensity must be WLED bytes (0-255).')
        payload={'on':True,'bri':round(self.config['music_brightness']*255),'transition':10,
                 'seg':[{'fx':effect,'sx':speed,'ix':intensity,'frz':False,'on':True,'bri':255,
                         'col':validated['colors'][:3]}]}
        self._stop_renderer(reset_chapter=False)
        self._post(payload)
        self._output_engine='native';self._native_effect=effect
        self._native_look={'colors':validated['colors'],'effect':effect,'engine':'native',
                           'native_speed':speed,'native_intensity':intensity}
        self._brightness=self.config['music_brightness'];self._mode='music'
        self._applied_tv=None

    def _chapter_audio(self, now, audio):
        """Compact recent mic measurements; never send raw audio or per-frame pixels."""
        if audio.get('active'):
            fft=audio.get('fft') or []
            if len(fft)==16:
                self._audio_history.append((now,float(audio.get('level',0)),tuple(float(v) for v in fft)))
        recent=[sample for sample in self._audio_history if now-sample[0]<=30]
        levels=[sample[1] for sample in recent]
        bands=[round(sum(sample[2][i] for sample in recent)/len(recent),3) if recent else 0.0
               for i in range(16)]
        renderer=self.renderer.status() if self.renderer else {}
        return {'window_s':30,'level_avg':round(sum(levels)/len(levels),3) if levels else 0.0,
                'level_peak':round(max(levels),3) if levels else 0.0,
                'beat_count':sum(now-beat_at<=30 for beat_at in self._chapter_beats),
                'fft_avg':bands,'bpm':renderer.get('bpm',0),
                'energy':renderer.get('energy',0), 'bass':renderer.get('bass',0),
                'mid':renderer.get('mid',0),'treble':renderer.get('treble',0)}

    def _maybe_chapter(self, obs, audio, now, decision):
        media=obs.get('media_session') or {}
        track=str(media.get('description') or '').strip()
        if track and track!=self._chapter_key:
            self._chapter_key=track;self._chapter_look={}
            self._chapter_generation+=1
            self._chapter_due_at=now if self._chapter_count else now+CHAPTER_FIRST_S
        elif self._chapter_key is None:
            self._chapter_key='ambient';self._chapter_due_at=now+CHAPTER_FIRST_S
        features=self._chapter_audio(now,audio)
        future=self._chapter_future
        if future is not None:
            if not future.done():return
            self._chapter_future=None
            try: chapter=future.result()
            except Exception as exc:
                chapter=None;self._chapter_error=str(exc)
            if (self._chapter_request_generation==self._chapter_generation
                    and self._chapter_request_key==self._chapter_key):
                try:
                    if not isinstance(chapter,dict):raise ValueError('Model returned no fresh chapter.')
                    engine=chapter.get('engine','ddp')
                    if engine=='native':
                        self._apply_native_chapter(chapter)
                        look=dict(self._native_look)
                    elif engine=='ddp':
                        _,look=_validate_show_request({'action':'tune',**{key:value for key,value in chapter.items() if key!='engine'}})
                        if not look:raise ValueError('Model returned an empty chapter.')
                        self._chapter_look=look;self._output_engine='ddp'
                        self._native_effect=None;self._native_look={}
                        self._look_dirty=True;self._music(decision,self._reason)
                        look={**look,'engine':'ddp'}
                    else:raise ValueError('Unknown music output engine.')
                    self._chapter_count+=1;self._chapter_error=None
                    self._chapter_history.append({key:look[key] for key in ('colors','motion','composition_mode','engine','effect') if key in look})
                except Exception as exc:
                    self._chapter_error=str(exc);self._chapter_due_at=now+CHAPTER_RETRY_S
            else:
                self._chapter_due_at=now
        if not audio.get('active') or not features['level_avg']>.025:return
        if self._chapter_due_at is None or now<self._chapter_due_at:return
        if self._chapter_executor is None:
            self._chapter_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='smart-music-chapter')
        if self._output_engine=='native':
            previous={**self._native_look,'recent':list(self._chapter_history)}
        elif self._last_recipe:
            previous={'colors':[list(c) for c in self._last_recipe[0]],
                      'motion':self._last_recipe[3], 'composition_mode':self._last_recipe[2],
                      'engine':'ddp','recent':list(self._chapter_history)}
        else:previous={}
        if self.chapter_fn is None:
            import music_chapters
            chapter_fn=music_chapters.design_chapter
        else:chapter_fn=self.chapter_fn
        request_key=self._chapter_key
        request_index=self._chapter_count+1
        self._chapter_request_key=request_key
        self._chapter_request_generation=self._chapter_generation
        def request_chapter():
            import music_chapters
            context={**previous,'native_effects':music_chapters.available_native_effects(self.fleet)}
            return chapter_fn(self.ai_settings,request_key,features,context,request_index)
        self._chapter_future=self._chapter_executor.submit(request_chapter)
        self._chapter_due_at=now+CHAPTER_INTERVAL_S

    def _manual_locked(self):
        self._stop_renderer();self._mode='manual';self._reason='Manual override; select Auto to resume.'

    def manual_override(self):
        with self._lock:
            changed=self.config['mode']!='manual';self.config['mode']='manual';self._manual_locked()
            if changed and self.persist_manual:
                try:
                    cfg=lightctl.load_config();section=dict(cfg.get('smart_director') or {});section['mode']='manual'
                    cfg['smart_director']=section;lightctl.save_config(cfg)
                except Exception as exc:self._error=f'Could not persist manual override: {exc}'

    def step(self, observation=None, decision=None, audio=None):
        with self._lock:
            if self._stop_event.is_set():return
            now=self.clock();obs=observation if observation is not None else self._observation
            if decision is None:decision={} if observation is not None else self._decision
            audio=audio if audio is not None else (self.listener.get_snapshot() if self.listener else {})
            if observation is None and now-self._observed_at>30:obs={};decision={}
            if not isinstance(obs,dict):obs={}
            if not isinstance(decision,dict):decision={}
            if not isinstance(audio,dict):audio={}
            seq=audio.get('receive_sequence')
            if seq is not None and seq!=self._last_seq:
                self._last_seq=seq
                if audio.get('beat'):
                    self._beat_times.append(now);self._chapter_beats.append(now)
            while self._beat_times and now-self._beat_times[0]>12:self._beat_times.popleft()
            sounding=bool(audio.get('active')) and float(audio.get('level',0))>.025
            if sounding:self._quiet_since=None
            elif self._quiet_since is None:self._quiet_since=now
            selected=self.config['mode'];desired=selected;reason='Explicit '+selected+' mode.'
            if selected=='auto':
                media=obs.get('media_session') or {}
                playing=(obs.get('connected') is True and obs.get('awake') is True and media.get('active') is True
                         and media.get('state')==3 and media.get('package')==obs.get('foreground_app'))
                observation_failed=not obs or bool(obs.get('error'))
                if observation_failed:
                    desired='tv';reason='TV observation unavailable; steady lighting.';self._last_music_at=None
                elif obs.get('connected') and obs.get('awake') is True and obs.get('activity_hint')=='tv':
                    desired='tv';reason='Video app: steady TV lighting.';self._last_music_at=None
                elif playing and decision.get('kind')=='tv':
                    desired='tv';reason=decision.get('reason') or 'Video playback identified.';self._last_music_at=None
                elif playing and (obs.get('activity_hint')=='music' or
                        (decision.get('kind')=='music' and decision.get('confidence',0)>=.85)):
                    desired='music';reason=decision.get('reason') or 'Music playback identified.';self._last_music_at=now
                elif not obs.get('connected') or obs.get('awake') is False:
                    desired='tv';reason='No confident TV context; steady lighting.';self._last_music_at=None
                elif self._mode=='music' and self._last_music_at is not None and now-self._last_music_at<self.config['unknown_hold_s']:
                    desired='music';reason='Holding music through a brief metadata gap.'
                else:desired='tv';reason='Uncertain TV content; steady lighting.'
                if self._quiet_since is not None and now-self._quiet_since>=self.config['silence_hold_s']:
                    desired='tv';reason='Controller microphone quiet or unavailable; steady lighting.'
                if desired=='music' and self._mode!='music':
                    if self._pending_mode!='music':self._pending_mode='music';self._pending_since=now
                    if now-self._pending_since<self.config['debounce_s']:desired='tv';reason='Confirming sustained music.'
                else:self._pending_mode=None
            try:
                if desired=='manual':self._manual_locked()
                elif desired=='music':
                    self._music(decision,reason)
                    if self._mode=='music':self._maybe_chapter(obs,audio,now,decision)
                else:self._tv(reason)
                self._error=self._renderer_fault
            except Exception as exc:self._error=str(exc)

    def status(self):
        with self._lock:
            try:audio=self.listener.get_snapshot() if self.listener else {'active':False}
            except Exception as exc:audio={'active':False,'error':str(exc)}
            try:renderer=self.renderer.status() if self.renderer else {'running':False,'sent_frames':0}
            except Exception as exc:renderer={'running':False,'sent_frames':0,'error':str(exc)}
            return {'running':self.is_alive() and not self._stop_event.is_set(),
                    'selected_mode':self.config['mode'],'mode':self._mode,'reason':self._reason,
                    'show_overrides':dict(self._show_overrides),
                    'output_engine':self._output_engine if self._mode=='music' else None,
                    'native_effect':self._native_effect if self._mode=='music' else None,
                    'chapters':{'applied':self._chapter_count,'track':self._chapter_key,
                                'pending':bool(self._chapter_future and not self._chapter_future.done()),
                                'last_error':self._chapter_error},
                    'brightness_percent':round(self._brightness*100,1),'audio':audio,'renderer':renderer,
                    'tv':dict(self._observation),'intelligence':dict(self._decision),'last_error':self._error}

    def wait_until_started(self, timeout=3.0):
        if not self._startup_event.wait(timeout):
            raise RuntimeError('Smart director startup timed out.')
        if self._startup_error is not None:
            raise RuntimeError(f'Smart director startup failed: {self._startup_error}')
        if not self.is_alive():
            raise RuntimeError('Smart director stopped during startup.')

    def run(self):
        try:
            if self.listener is None:self.listener=self._make_listener()
            self._listener_stopped=False
            self.listener.start()
            self._observer=threading.Thread(target=self._observer_loop,name='smart-tv-observer',daemon=True)
            self._observer.start();self._observer_started=True
            self._startup_event.set()
            while not self._stop_event.is_set():
                self.step()
                self._stop_event.wait(.2)
        except Exception as exc:
            with self._lock:
                self._error=str(exc)
                if not self._startup_event.is_set():self._startup_error=str(exc)
        finally:
            self._startup_event.set()
            self._stop_event.set()
            try:self._cleanup_components()
            finally:
                if self._chapter_executor is not None:
                    self._chapter_executor.shutdown(wait=False,cancel_futures=True)
                    self._chapter_executor=None
                renderer=self.renderer
                renderer_alive=bool(renderer and renderer.is_alive())
                if not renderer_alive:_release_process_lock(self)
                else:
                    with self._lock:self._error='Smart director stopped but renderer is still running; ownership retained.'

    def shutdown(self):
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:self.join(timeout=5)
        if self.is_alive():
            with self._lock:self._error='Smart director did not stop cleanly.'
            raise RuntimeError(self._error)
        self._cleanup_components()
        if self._chapter_executor is not None:
            self._chapter_executor.shutdown(wait=False,cancel_futures=True)
            self._chapter_executor=None
        if self.renderer is not None and self.renderer.is_alive():
            raise RuntimeError('Smart director renderer is still running.')


_director=None
_registry_lock=threading.RLock()
_process_lock_guard=threading.Lock()
_process_lock_file=None
_process_lock_owner=None


def _acquire_process_lock():
    global _process_lock_file
    directory=os.path.dirname(lightctl._CONFIG_PATH)
    os.makedirs(directory,exist_ok=True)
    handle=open(os.path.join(directory,'smart-director.lock'),'a+',encoding='utf-8')
    try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError('another instance owns automatic lighting') from None
    with _process_lock_guard:_process_lock_file=handle


def _release_process_lock(owner=None):
    global _process_lock_file,_process_lock_owner
    with _process_lock_guard:
        if owner is not None and _process_lock_owner is not owner:return
        handle=_process_lock_file;_process_lock_file=None;_process_lock_owner=None
    if handle is not None:
        try:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()


def current():
    with _registry_lock:return _director


def start(fleet, config=None, ai_settings=None):
    global _director,_process_lock_owner
    cfg=validate_config(config)
    with _registry_lock:
        if _director and _director.is_alive():
            import mcp_light
            mcp_light._stop_timers();_director.configure({k:v for k,v in cfg.items() if v != _director.config.get(k)});return _director
        _acquire_process_lock()
        try:
            import mcp_light,music_director,realtime,shows
            mcp_light._stop_timers();music_director.stop_director();shows.stop_show();realtime.realtime_stop()
            _director=SmartDirector(fleet,cfg,ai_settings=ai_settings,persist_manual=True)
            _process_lock_owner=_director
            _director.start();_director.wait_until_started();return _director
        except Exception as startup_error:
            failed=_director
            cleanup_error=None
            if failed is not None:
                try:failed.shutdown()
                except Exception as exc:cleanup_error=exc
            try:renderer_alive=bool(failed and failed.renderer and failed.renderer.is_alive())
            except Exception:renderer_alive=True
            if failed is not None and (failed.is_alive() or renderer_alive):
                _director=failed
                raise RuntimeError('Smart director startup failed but ownership is still active; ownership retained.') from (cleanup_error or startup_error)
            _director=None;_release_process_lock()
            raise startup_error


def stop(steady=True):
    global _director
    with _registry_lock:
        d=_director
        if d is None:return
        failure=None
        try:
            if steady:
                d.configure({'mode':'tv'});d.step({}, {}, {})
            d.shutdown()
        except Exception as exc:
            failure=exc
        try:renderer_alive=bool(d.renderer and d.renderer.is_alive())
        except Exception:renderer_alive=True
        if d.is_alive() or renderer_alive:
            raise RuntimeError('Smart director or renderer is still running; ownership retained.') from failure
        _director=None;_release_process_lock(d)
        if failure is not None:raise failure


def before_external_write(fleet):
    # Never acquire the registry lock from a fleet write: stop() may be joining
    # the director thread while holding it. The object reference is sufficient.
    d=_director
    if d is not None and d.fleet is fleet and not getattr(_write_context,'owned',False):
        d.manual_override()


def status():
    d=current()
    return d.status() if d else {'running':False,'mode':'off','audio':{'active':False},'renderer':{'running':False}}


def _validate_show_request(request):
    """Validate atomically before touching the running renderer."""
    if not isinstance(request, dict):
        raise ValueError('Live show controls must be an object.')
    action=request.get('action', 'status')
    if not isinstance(action, str) or action not in {'status','tune','accent','native','ddp'}:
        raise ValueError('action must be status, tune, accent, native, or ddp.')
    allowed={'action'}
    if action=='accent':allowed |= {'band','strength'}
    if action=='tune':allowed |= {'motion','speed','brightness','intensity','colorfulness','colors','composition_mode','band_gains'}
    if action=='native':allowed |= {'effect','colors','native_speed','native_intensity'}
    if set(request)-allowed:raise ValueError('Unknown live show control: '+', '.join(sorted(set(request)-allowed)))
    updates={k:v for k,v in request.items() if k!='action'}
    if action=='accent':
        if type(updates.get('band')) is not int or not 0 <= updates['band'] <= 15:
            raise ValueError('band must be an integer from 0 through 15.')
        updates.setdefault('strength',1.0)
    bounds={'speed':(.25,4),'brightness':(0,1),'intensity':(0,1),'colorfulness':(0,1),'strength':(0,1)}
    def number(value,low,high):
        return not isinstance(value,bool) and isinstance(value,(int,float)) and low<=value<=high and math.isfinite(value)
    for key,(low,high) in bounds.items():
        if key in updates and not number(updates[key],low,high):raise ValueError(f'{key} must be between {low} and {high}.')
    if 'motion' in updates:
        import realtime
        if not isinstance(updates['motion'],str) or updates['motion'] not in realtime.AudioReactiveRunner.MOTIONS:
            raise ValueError('Invalid music motion.')
    if 'composition_mode' in updates and (not isinstance(updates['composition_mode'],str) or updates['composition_mode'] not in color_lab.COMPOSITION_MODES):
        raise ValueError('Invalid composition_mode.')
    if 'band_gains' in updates:
        gains=updates['band_gains']
        if not isinstance(gains,list) or len(gains)!=16 or not all(number(v,0,3) for v in gains):
            raise ValueError('band_gains must contain 16 numbers between 0 and 3.')
        updates['band_gains']=list(gains)
    if 'colors' in updates:
        colors=updates['colors']
        if not isinstance(colors,list) or not 1<=len(colors)<=5 or not all(
                isinstance(rgb,list) and len(rgb)==3 and all(type(v) is int and 0<=v<=210 for v in rgb) for rgb in colors):
            raise ValueError('colors must contain 1 through 5 RGB triplets with integers from 0 through 210.')
        updates['colors']=[list(rgb) for rgb in colors]
    if action=='native':
        if type(updates.get('effect')) is not int or updates['effect']<0:
            raise ValueError('effect must be a nonnegative WLED effect ID.')
        if 'colors' not in updates:raise ValueError('native requires colors.')
        for key in ('native_speed','native_intensity'):
            if key in updates and (type(updates[key]) is not int or not 0<=updates[key]<=255):
                raise ValueError(f'{key} must be a WLED byte (0-255).')
    return action,updates


def control_show(fleet, request):
    """Control the current music owner or deliberately hand off between engines."""
    action,updates=_validate_show_request(request)
    director=current()
    if action=='status':
        return {'ok':True,'status':director.status() if director and director.fleet is fleet else {'running':False,'mode':'off'}}
    if director is None or director.fleet is not fleet:
        raise RuntimeError('Start Smart Director music mode before using live show controls.')
    with director._lock:
        if director._mode!='music' or director._stop_event.is_set():
            raise RuntimeError('Start Smart Director music mode before using live show controls.')
        if action=='native':
            director._apply_native_chapter(updates)
            director._chapter_generation+=1
        elif action=='ddp':
            director._output_engine='ddp';director._native_effect=None;director._native_look={}
            director._music(director._decision,director._reason)
            director._chapter_generation+=1
        elif not director.renderer or not director.renderer.is_alive():
            raise RuntimeError('Start Smart Director DDP music renderer before tuning or accenting it.')
        elif action=='accent':
            director.renderer.accent(**updates)
        else:
            director.renderer.update_look(**updates)
            director._show_overrides.update(updates)
            director._look_dirty=True
            if 'brightness' in updates:director._brightness=updates['brightness']
        return {'ok':True,'status':director.status()}
