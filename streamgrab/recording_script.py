"""Playback capture inside the selected app-owned browser tab."""

SELECT_PLAYER = r"""(key) => {
  const candidates = [...document.querySelectorAll('video')].filter(v => {
    const r = v.getBoundingClientRect();
    return v.readyState >= 2 && v.videoWidth > 0 && !v.error && r.width > 1 && r.height > 1;
  });
  candidates.sort((a, b) => {
    const area = v => {const r = v.getBoundingClientRect();
      return Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left)) *
             Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top));};
    return area(b) - area(a);
  });
  const video = candidates[0];
  if (!video) return {found: false};
  window[key] = video;
  return {found: true, title: document.title, duration: Number.isFinite(video.duration) ? video.duration : null,
    width: video.videoWidth, height: video.videoHeight, protected: !!video.mediaKeys, url: location.href};
}"""

START_RECORDING = r"""(options) => {
  const video = window[options.playerKey];
  const send = (kind, data = {}) => window[options.binding](JSON.stringify({token: options.token, kind, ...data}));
  let recorder, timer, stopped = false, failure = '', sequence = 0, bytes = 0, pending = Promise.resolve();
  const listeners = [];
  const listen = (event, action) => { video.addEventListener(event, action); listeners.push([event, action]); };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    if (video) video.pause();
  };
  window[options.stopKey] = stop;
  const fail = code => { failure = code; stop(); if (!recorder) send('error', {code}); };
  const read = blob => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result), marker = ';base64,', offset = value.indexOf(marker);
      if (offset < 0) reject(Error('transfer_failed'));
      else resolve(value.slice(offset + marker.length));
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  (async () => {
    if (!video || !video.isConnected) return fail('no_player');
    if (video.mediaKeys) return fail('protected_media');
    if (typeof video.captureStream !== 'function' || typeof MediaRecorder === 'undefined') return fail('unsupported');
    video.pause();
    video.muted = true;
    video.playbackRate = 1;
    video.loop = false;
    if (Math.abs(video.currentTime - options.start) > 0.04) {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => { video.removeEventListener('seeked', done); reject(Error('seek_failed')); }, 10000);
        const done = () => { clearTimeout(timeout); video.removeEventListener('seeked', done); resolve(); };
        video.addEventListener('seeked', done);
        try { video.currentTime = options.start; } catch (_) { clearTimeout(timeout); reject(Error('seek_failed')); }
      });
    }
    if (stopped) return;
    let stream;
    try { stream = video.captureStream(); } catch (_) { return fail('capture_denied'); }
    if (options.mode === 'audio') stream = new MediaStream(stream.getAudioTracks());
    if (!stream.getTracks().length || (options.mode === 'video' && !stream.getVideoTracks().length)) return fail('missing_track');
    const choices = options.mode === 'audio' ? ['audio/webm;codecs=opus', 'audio/webm'] :
      ['video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus', 'video/webm'];
    const mimeType = choices.find(type => MediaRecorder.isTypeSupported(type));
    if (!mimeType) return fail('unsupported');
    recorder = new MediaRecorder(stream, {mimeType, videoBitsPerSecond: 5000000, audioBitsPerSecond: 192000});
    recorder.ondataavailable = event => {
      if (!event.data.size) return;
      bytes += event.data.size;
      pending = pending.then(async () => {
        for (let offset = 0; offset < event.data.size; offset += 65536) {
          const data = await read(event.data.slice(offset, offset + 65536));
          send('data', {sequence: sequence++, data});
        }
      });
    };
    recorder.onerror = () => { failure = 'recording_failed'; stop(); };
    recorder.onstop = async () => {
      clearInterval(timer);
      for (const [event, action] of listeners) video.removeEventListener(event, action);
      try {
        await pending;
        if (!failure && video.currentTime < options.end - 0.3) failure = 'playback_changed';
        if (failure) send('error', {code: failure});
        else send('done', {bytes, chunks: sequence, elapsed: video.currentTime - options.start});
      } catch (_) { send('error', {code: 'transfer_failed'}); }
      for (const track of stream.getTracks()) track.stop();
      video.pause();
    };
    listen('waiting', () => { if (recorder.state === 'recording') recorder.pause(); });
    listen('playing', () => { if (recorder.state === 'paused') recorder.resume(); });
    listen('error', () => fail('media_error'));
    listen('ended', stop);
    const source = video.currentSrc;
    let lastTime = video.currentTime, lastChange = performance.now(), lastReport = 0;
    recorder.start(500);
    try { await video.play(); } catch (_) { return fail('play_failed'); }
    if (stopped) return;
    send('started', {mimeType, videoTracks: stream.getVideoTracks().length, audioTracks: stream.getAudioTracks().length});
    timer = setInterval(() => {
      const now = performance.now(), current = video.currentTime;
      if (!video.isConnected || video.currentSrc !== source) return fail('playback_changed');
      if (current > lastTime + 0.01) {
        lastTime = current; lastChange = now;
        if (!video.paused && recorder.state === 'paused') recorder.resume();
      }
      if (now - lastChange > 15000) return fail('stalled');
      if (current >= options.end || video.ended) return stop();
      if (now - lastReport >= 300) { lastReport = now; send('progress', {elapsed: current - options.start}); }
    }, 40);
  })().catch(error => fail(['seek_failed', 'stalled', 'media_error'].includes(error.message) ? error.message : 'recording_failed'));
  return true;
}"""
