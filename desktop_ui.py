"""Desktop library and reviewed calibration UI. No periodic model calls."""

DIALOGS = '''
<dialog id="calibrationDialog" style="width:min(850px,92vw);max-height:85vh;overflow:auto;background:#12172b;color:#eef2ff;border:1px solid #64748b;border-radius:18px;padding:24px;">
  <h2>Calibrate LEDs</h2>
  <p>Read controller configuration, review the proposed topology, then apply it to Lightss. Electrical settings are never guessed or rewritten.</p>
  <div class="row"><button onclick="scanCalibration()">Scan again</button><button id="calibrationApply" disabled onclick="applyCalibration()">Apply reviewed topology</button><button class="secondary" onclick="closeCalibration()">Close</button></div>
  <p id="calibrationStatus" role="status"></p>
  <div id="calibrationFacts"></div>
  <details><summary>Proposed platform configuration</summary><pre id="calibrationProposal" style="white-space:pre-wrap;"></pre></details>
  <hr><h3>Optional visual check</h3>
  <p>Use an Identify button to mark one segment briefly. Camera analysis is one opt-in model request; it cannot prove wiring, exact counts, or pixel-zero direction and never applies changes.</p>
  <div class="row"><button id="calibrationCamera" onclick="toggleCalibrationCamera()">Enable camera</button><button id="calibrationVision" disabled onclick="analyzeCalibrationFrame()">Analyze one frame</button></div>
  <video id="calibrationVideo" autoplay muted playsinline style="display:none;max-width:100%;width:480px;"></video>
  <p id="calibrationObservation" style="white-space:pre-wrap;" role="status"></p>
</dialog>
'''

LIBRARY = '''
<section class="card" style="margin-top:16px;">
  <h3>My playlists &amp; queue</h3>
  <p class="card-note">Saved on this device. Sequential queues use individual tracks; saved albums and artists open in the provider. Account playlists stay in the Account library.</p>
  <div class="row"><button onclick="playerCommand('library')">Account library</button><button class="secondary" onclick="loadLocalLibrary()">Refresh saved</button></div>
  <div class="row" style="margin-top:10px;"><input id="localPlaylistName" placeholder="Playlist name" maxlength="120"><button onclick="editLocalPlaylist('create')">Create</button></div>
  <div class="row" style="margin-top:10px;"><select id="localPlaylistSelect" onchange="renderLocalLibrary()" aria-label="Saved playlist"></select><button onclick="editLocalPlaylist('rename')">Rename</button><button class="secondary" onclick="editLocalPlaylist('delete')">Delete</button><button onclick="playSavedPlaylist()">Play playlist</button></div>
  <div class="row" style="margin-top:10px;"><button onclick="saveCurrentTrack(false)">Save current to playlist</button><button onclick="saveCurrentTrack(true)">Queue current</button></div>
  <details style="margin-top:10px;"><summary>Add by provider link or ID</summary>
    <div class="row"><input id="localTrackInput" placeholder="Track / album / playlist URL or ID"><select id="localTrackKind"><option value="song">Track</option><option value="album">Album</option><option value="playlist">Playlist</option><option value="artist">Artist</option></select><button onclick="saveTrackInput()">Add to playlist</button></div>
  </details>
  <p id="localLibraryStatus" role="status"></p><div id="localPlaylistItems" style="max-height:260px;overflow:auto;"></div>
  <h3>Queue</h3><div class="row"><button onclick="playLocalQueue(0)">Play queue</button><button class="secondary" onclick="stopLocalQueue()">Stop queue</button><button class="secondary" onclick="mutateLocalLibrary({action:'queue_clear'})">Clear</button></div>
  <div id="localQueue" style="max-height:260px;overflow:auto;margin-top:8px;"></div>
</section>
'''

SCRIPT = r'''
let calibrationReview = null;
let calibrationStream = null;
let calibrationBusy = false;
let localLibrary = {playlists: [], queue: []};
let localQueueRun = null;

function uiButton(label, callback) {
  const button = document.createElement('button');
  button.className = 'secondary'; button.textContent = label; button.onclick = callback;
  return button;
}

async function openCalibration() {
  const dialog = document.getElementById('calibrationDialog');
  if (!dialog.open) dialog.showModal();
  await scanCalibration();
}

function closeCalibration() {
  stopCalibrationCamera();
  document.getElementById('calibrationDialog').close();
}

async function scanCalibration() {
  if (calibrationBusy) return;
  calibrationBusy = true; calibrationReview = null;
  document.getElementById('calibrationApply').disabled = true;
  setText('calibrationStatus', 'Reading every configured controller…');
  try {
    const data = await postJson('/api/calibration', {action:'scan'});
    calibrationReview = data.ok && data.can_apply ? data : null;
    document.getElementById('calibrationApply').disabled = !calibrationReview;
    setText('calibrationStatus', data.message || data.error || 'Scan complete. Review before applying.');
    setText('calibrationProposal', JSON.stringify(data.proposed || {}, null, 2));
    const facts = document.getElementById('calibrationFacts'); facts.replaceChildren();
    for (const [name, probe] of Object.entries(data.probes || {})) {
      const section = document.createElement('section');
      const title = document.createElement('h3'); title.textContent = name; section.append(title);
      const text = document.createElement('p');
      text.textContent = probe.error || `${probe.led_count ?? '?'} declared LEDs · WLED ${probe.version || '?'} · ${probe.host || ''}`;
      section.append(text);
      for (const bus of probe.buses || []) {
        const line = document.createElement('p');
        line.textContent = `GPIO ${bus.gpio ?? '?'} · pixels ${bus.start}–${Number(bus.start)+Number(bus.len)-1} · ${bus.color_order || 'unknown order'}`;
        section.append(line);
      }
      for (const segment of probe.segments || []) {
        const line = document.createElement('div'); line.className = 'row';
        const label = document.createElement('span');
        label.textContent = `Segment ${segment.id}: [${segment.start}, ${segment.stop}) · GPIO ${segment.gpio ?? '?'}`;
        line.append(label, uiButton('Identify briefly', () => identifyCalibration(name, segment.id))); section.append(line);
      }
      facts.append(section);
    }
  } catch (err) { setText('calibrationStatus', 'Scan failed: ' + err.message); }
  finally { calibrationBusy = false; }
}

async function applyCalibration() {
  if (!calibrationReview || calibrationBusy) return;
  const token = calibrationReview.token; calibrationReview = null; calibrationBusy = true;
  document.getElementById('calibrationApply').disabled = true;
  try {
    const data = await postJson('/api/calibration', {action:'apply', token});
    setText('calibrationStatus', (data.message || data.error || 'Apply finished.') + (data.config_backup ? '\nBackup: '+data.config_backup : ''));
  } catch (err) { setText('calibrationStatus', 'Apply failed: ' + err.message); }
  finally { calibrationBusy = false; }
}

async function identifyCalibration(controller, segment) {
  if (calibrationBusy || !confirm('Briefly illuminate only this segment at low brightness? Stop active lighting streams first.')) return;
  calibrationBusy = true;
  try {
    const data = await postJson('/api/calibration', {action:'identify', controller, segment});
    setText('calibrationStatus', data.message || data.error);
  } catch (err) { setText('calibrationStatus', err.message); }
  finally { calibrationBusy = false; }
}

function stopCalibrationCamera() {
  if (calibrationStream) calibrationStream.getTracks().forEach(track => track.stop());
  calibrationStream = null;
  const video = document.getElementById('calibrationVideo'); video.srcObject = null; video.style.display = 'none';
  document.getElementById('calibrationVision').disabled = true;
  setText('calibrationCamera', 'Enable camera');
}

async function toggleCalibrationCamera() {
  if (calibrationStream) { stopCalibrationCamera(); return; }
  try {
    calibrationStream = await navigator.mediaDevices.getUserMedia({video:{width:640,height:480},audio:false});
    if (!document.getElementById('calibrationDialog').open) { stopCalibrationCamera(); return; }
    const video = document.getElementById('calibrationVideo'); video.srcObject = calibrationStream; video.style.display = 'block';
    document.getElementById('calibrationVision').disabled = false;
    setText('calibrationCamera', 'Disable camera');
  } catch (err) { setText('calibrationObservation', 'Camera unavailable: ' + err.message); }
}

async function analyzeCalibrationFrame() {
  if (!calibrationStream) return;
  const button = document.getElementById('calibrationVision'); button.disabled = true;
  try {
    const video = document.getElementById('calibrationVideo');
    if (!video.videoWidth) throw new Error('Wait for the camera preview.');
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
    canvas.getContext('2d').drawImage(video,0,0,640,480);
    setText('calibrationObservation', 'Analyzing one frame. No lights or settings will be changed…');
    const data = await fetchJsonWithTimeout('/api/calibration/vision', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:canvas.toDataURL('image/jpeg',0.75).split(',')[1]})},45000);
    setText('calibrationObservation', data.observation || data.error || data.message);
  } catch (err) { setText('calibrationObservation', err.message); }
  finally { button.disabled = !calibrationStream; }
}

async function loadLocalLibrary() {
  try {
    const data = await fetchJsonWithTimeout('/api/player/library',{},5000);
    if (!data.ok) throw new Error(data.error || data.message || 'Library unavailable');
    localLibrary = data; renderLocalLibrary();
  } catch (err) { setText('localLibraryStatus',err.message); }
}

async function mutateLocalLibrary(payload) {
  try {
    const data = await postJson('/api/player/library',payload);
    if (!data.ok) throw new Error(data.error || data.message || 'Library update failed');
    localQueueRun = null; localLibrary = data; renderLocalLibrary();
    setText('localLibraryStatus',data.message || 'Saved on this device. Queue sequencing stopped after edit.');
    return data;
  } catch (err) { setText('localLibraryStatus',err.message); return null; }
}

function selectedLocalPlaylist() {
  const id = document.getElementById('localPlaylistSelect').value;
  return (localLibrary.playlists || []).find(item => item.id === id);
}

function renderLocalLibrary() {
  const select = document.getElementById('localPlaylistSelect'); if (!select) return;
  const previous = select.value; select.replaceChildren();
  for (const playlist of localLibrary.playlists || []) {
    const option = document.createElement('option'); option.value = playlist.id; option.textContent = playlist.name; select.append(option);
  }
  if ((localLibrary.playlists || []).some(item => item.id === previous)) select.value = previous;
  const playlist = selectedLocalPlaylist();
  renderLocalItems(document.getElementById('localPlaylistItems'), playlist ? playlist.items : [], playlist ? playlist.id : null);
  renderLocalItems(document.getElementById('localQueue'), localLibrary.queue || [], null);
}

function renderLocalItems(container, items, playlistId) {
  if (!container) return;
  container.replaceChildren();
  items.forEach((item,index) => {
    const row = document.createElement('div'); row.className = 'row'; row.style.marginBottom = '8px';
    const title = document.createElement('span'); title.style.flex = '1'; title.textContent = (item.title || item.provider_id) + (item.artist ? ' — '+item.artist : '') + ' · '+item.source;
    row.append(title,uiButton('Play',()=> playlistId ? playLocalItem(item) : playLocalQueue(index)));
    if (playlistId) { const add = uiButton('Queue',()=>mutateLocalLibrary({action:'queue_add',item})); add.disabled = item.kind !== 'song'; row.append(add); }
    for (const [label,to] of [['↑',index-1],['↓',index+1]]) {
      const button = uiButton(label,()=>mutateLocalLibrary({action:playlistId?'reorder':'queue_reorder',playlist_id:playlistId,from_index:index,to_index:to}));
      button.disabled = to < 0 || to >= items.length; row.append(button);
    }
    row.append(uiButton('Remove',()=>mutateLocalLibrary({action:playlistId?'remove':'queue_remove',playlist_id:playlistId,index})));
    container.append(row);
  });
}

async function editLocalPlaylist(action) {
  const name = document.getElementById('localPlaylistName').value.trim();
  const playlist = selectedLocalPlaylist();
  if (action !== 'create' && !playlist) return;
  if (action === 'delete' && !confirm('Delete this local playlist? Your service-account library is unchanged.')) return;
  await mutateLocalLibrary({action,name,playlist_id:playlist && playlist.id});
}

async function saveCurrentTrack(queue) {
  const status = lastPlayerStatus || {};
  if (!status.track_id) { setText('localLibraryStatus','This source has not reported a track ID. Paste a provider link instead.'); return; }
  const item = {source:status.source || currentPlayerSource(),provider_id:status.track_id,kind:'song',title:status.title || '',artist:status.artist || '',album:status.album || ''};
  const playlist = selectedLocalPlaylist();
  if (!queue && !playlist) { setText('localLibraryStatus','Create or select a local playlist first.'); return; }
  await mutateLocalLibrary({action:queue?'queue_add':'add',playlist_id:playlist && playlist.id,item});
}

async function saveTrackInput() {
  const playlist = selectedLocalPlaylist(); if (!playlist) { setText('localLibraryStatus','Create a playlist first.'); return; }
  let id = document.getElementById('localTrackInput').value.trim();
  let source = currentPlayerSource(), kind = document.getElementById('localTrackKind').value;
  try {
    if (id.includes('://')) {
      const url = new URL(id);
      if (url.protocol !== 'https:') throw new Error('Use an HTTPS music link.');
      if (url.hostname === 'music.youtube.com' || url.hostname === 'www.youtube.com') {
        source = 'youtube_music';
        if (url.searchParams.get('v')) { id=url.searchParams.get('v'); kind='song'; }
        else if (url.searchParams.get('list')) { id=url.searchParams.get('list'); kind='playlist'; }
        else { id=url.pathname.split('/').filter(Boolean).pop(); }
      } else if (url.hostname === 'music.apple.com') {
        source='apple_music'; const parts=url.pathname.split('/').filter(Boolean);
        id=url.searchParams.get('i') || parts[parts.length-1];
        kind=url.searchParams.get('i')?'song':(parts.includes('album')?'album':parts.includes('playlist')?'playlist':parts.includes('artist')?'artist':kind);
      } else throw new Error('Use a YouTube Music or Apple Music link.');
    }
    await mutateLocalLibrary({action:'add',playlist_id:playlist.id,item:{source,provider_id:id,kind,title:id}});
  } catch (err) { setText('localLibraryStatus',err.message); }
}

async function playLocalItem(item) {
  const source = document.getElementById('playerSource'); if (source) source.value = item.source;
  const data = await postJson('/api/player',{source:item.source,command:'playItem',data:{id:item.provider_id,kind:item.kind}});
  if (!data.ok) throw new Error(data.error || data.message || 'Playback failed');
  if (data.client_play) {
    const handled = await handleAppleClientCommand('playItem',data.client_play);
    if (!handled || !handled.ok) throw new Error(handled?.message || 'MusicKit playback unavailable');
  }
  setText('localLibraryStatus',data.message || 'Playback requested.');
  return data;
}

async function playLocalQueue(index) {
  const item = (localLibrary.queue || [])[index]; if (!item) { localQueueRun=null; return; }
  if (item.kind !== 'song') { localQueueRun=null;setText('localLibraryStatus','Sequential queues require individual tracks. Open this collection in its provider.');return; }
  localQueueRun = {index,source:item.source,id:item.provider_id,seen:false,busy:true};
  try { await playLocalItem(item); if (localQueueRun) localQueueRun.busy=false; }
  catch (err) { localQueueRun=null; setText('localLibraryStatus',err.message); }
}

async function maybeAdvanceLocalQueue(status) {
  const run = localQueueRun;
  if (!run || run.busy || status.source !== run.source) return;
  if (!run.seen && status.track_id === run.id && status.playing) {
    run.seen=true; run.endedSequence=Number(status.ended_sequence || 0);
  }
  const retainedEnd = status.last_ended_track_id === run.id && Number(status.ended_sequence || 0) > Number(run.endedSequence || 0);
  const immediateEnd = status.track_id === run.id && status.ended && status.ended_sequence === undefined;
  if (run.seen && (retainedEnd || immediateEnd)) { run.busy=true; await playLocalQueue(run.index+1); }
}

async function stopLocalQueue() {
  const run = localQueueRun; localQueueRun=null;
  if (run) await postJson('/api/player',{source:run.source,command:'pause'});
}

async function playSavedPlaylist() {
  const playlist = selectedLocalPlaylist(); if (!playlist || !playlist.items.length) return;
  if (!confirm('Replace the local queue with this playlist and start playback?')) return;
  if (!await mutateLocalLibrary({action:'queue_from_playlist',playlist_id:playlist.id})) return;
  await playLocalQueue(0);
}
'''
