// Upload spike (epic-platform entry 2): Uppy 6 with @uppy/aws-s3 `signRequest`. Every S3
// request is signed by the site (POST <sign-url>); the browser never holds bucket keys.
// After each upload the object is read back from the bucket (POST <object-url>) and its
// key, size and ETag are shown. Entry 11 deletes this file.
(function () {
  'use strict';

  var root = document.getElementById('upload-spike');
  var logBox = document.getElementById('upload-spike-log');
  var results = document.getElementById('upload-spike-results');
  if (!root) return;

  var MiB = 1024 * 1024;
  var signUrl = root.dataset.signUrl;
  var objectUrl = root.dataset.objectUrl;
  var csrfToken = root.dataset.csrfToken;
  var started = {};
  var SESSION_EXPIRED = 'session expired: reload the page and sign in';

  function log(message) {
    var line = new Date().toISOString() + '  ' + message + '\n';
    logBox.textContent += line;
    logBox.scrollTop = logBox.scrollHeight;
  }

  if (!window.Uppy || !window.Uppy.AwsS3) {
    log('Uppy did not load (static/js/uppy.min.js).');
    return;
  }

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(body),
    }).then(function (response) {
      // A redirect (the login page after the idle limit) or any non-JSON answer means the
      // session is gone, not that OVH refused something.
      if (response.redirected) throw new Error(SESSION_EXPIRED);
      return response.json().catch(function () { return null; }).then(function (data) {
        if (!response.ok) {
          var error = data && data.error;
          throw new Error(error ? error.code + ': ' + error.message : 'HTTP ' + response.status);
        }
        if (!data || typeof data !== 'object') throw new Error(SESSION_EXPIRED);
        return data;
      });
    });
  }

  function describe(request) {
    if (request.partNumber) return 'UploadPart ' + request.partNumber;
    if (!request.uploadId) return request.method === 'PUT' ? 'PutObject' : 'CreateMultipartUpload';
    return { GET: 'ListParts', POST: 'CompleteMultipartUpload', DELETE: 'AbortMultipartUpload' }[request.method]
      || request.method;
  }

  function addResult(file, stored, seconds) {
    var item = document.createElement('li');
    var rows = [
      ['File', file.name + ' (' + file.size + ' bytes local)'],
      ['Key', stored.key],
      ['Size', stored.size + ' bytes' + (stored.size === file.size ? ' (matches)' : ' (DIFFERS from local)')],
      ['ETag', stored.etag],
      ['Encryption', stored.serverSideEncryption || '(none reported)'],
      ['Time', seconds.toFixed(1) + ' s, ' + (file.size / MiB / Math.max(seconds, 0.001)).toFixed(1) + ' MiB/s'],
    ];
    rows.forEach(function (row) {
      var div = document.createElement('div');
      var label = document.createElement('strong');
      label.textContent = row[0] + ': ';
      div.appendChild(label);
      div.appendChild(document.createTextNode(row[1]));
      item.appendChild(div);
    });
    var link = document.createElement('a');
    link.href = stored.downloadUrl;
    link.textContent = 'Download (for shasum -a 256)';
    link.rel = 'noopener';
    item.appendChild(link);
    results.prepend(item);
  }

  var uppy = new window.Uppy.Uppy({ autoProceed: false });

  uppy.use(window.Uppy.Dashboard, {
    inline: true,
    target: root,
    width: '100%',
    height: 360,
    showProgressDetails: true,
    proudlyDisplayPoweredByUppy: false,
  });

  uppy.use(window.Uppy.AwsS3, {
    // Small files take the single presigned PUT; the rest S3 multipart (parts ≥ 5 MiB).
    shouldUseMultipart: function (file) { return (file.size || 0) > 5 * MiB; },
    // No file name or metadata leaves the browser; the site replaces this key anyway.
    allowedMetaFields: false,
    generateObjectKey: function () { return crypto.randomUUID(); },
    signRequest: function (request) {
      var body = { method: request.method, key: request.key };
      if (request.uploadId) body.uploadId = request.uploadId;
      if (request.partNumber) body.partNumber = request.partNumber;
      if (request.contentType) body.contentType = request.contentType;
      var name = describe(request);
      return postJson(signUrl, body).then(function (signed) {
        if (typeof signed.url !== 'string' || !signed.url) {
          throw new Error('the site sent no signed URL (' + SESSION_EXPIRED + ')');
        }
        if (!request.partNumber) log('signed ' + name + (signed.key ? ' -> ' + signed.key : ' ' + request.key));
        return signed;
      }).catch(function (error) {
        log('signing ' + name + ' failed: ' + error.message);
        throw error;
      });
    },
  });

  uppy.on('upload-start', function (files) {
    files.forEach(function (file) {
      started[file.id] = Date.now();
      log('start ' + file.name + ' (' + file.size + ' bytes)');
    });
  });
  uppy.on('s3-multipart:part-uploaded', function (file, part) {
    log('part ' + part.PartNumber + ' uploaded, ETag ' + part.ETag);
  });
  uppy.on('upload-success', function (file, response) {
    var seconds = (Date.now() - (started[file.id] || Date.now())) / 1000;
    var key = response && response.body && response.body.key;
    log('uploaded ' + file.name + ' as ' + key + ' in ' + seconds.toFixed(1) + ' s');
    postJson(objectUrl, { key: key }).then(function (stored) {
      log('bucket has ' + stored.key + ': ' + stored.size + ' bytes, ETag ' + stored.etag
          + ', encryption ' + (stored.serverSideEncryption || '(none reported)'));
      addResult(file, stored, seconds);
    }, function (error) {
      log('reading ' + key + ' back failed: ' + error.message);
    });
  });
  uppy.on('upload-error', function (file, error) {
    log('upload of ' + (file ? file.name : '?') + ' failed: ' + (error && error.message));
  });
  uppy.on('upload-retry', function (file) {
    log('retry ' + (file && file.name ? file.name : ''));
  });
  uppy.on('error', function (error) { log('error: ' + (error && error.message)); });

  window.addEventListener('offline', function () { log('browser went offline'); });
  window.addEventListener('online', function () { log('browser is back online'); });
  log('ready (Uppy ' + (window.Uppy.Uppy.VERSION || '?') + ', aws-s3 '
      + (window.Uppy.AwsS3.VERSION || '?') + ')');
})();
