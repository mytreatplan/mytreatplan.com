// The upload widget (storage/_upload_widget.html; seeded from the upload spike's page).
// Uppy 6 with @uppy/aws-s3 `signRequest`: every S3 request is signed by the site
// (POST <sign-url>); the browser never holds bucket keys. Every upload is S3 multipart in
// 16 MiB parts. Starting one declares its purpose, size and type (the site checks the
// purpose's permission and size limit before signing anything); the site makes the key.
// When S3 has completed it, POST <complete-url> records it (the site checks the stored
// size) with the file's name, which goes nowhere else. A part refused with 403 (OVHcloud
// applying a new policy, or an expired URL) is retried once.
(function () {
  'use strict';

  var MiB = 1024 * 1024;
  var PART_SIZE = 16 * MiB;
  // Files uploading at the same time (@uppy/aws-s3 6.3.0 sends one file's parts one after
  // another; `limit` is how many requests run at once across files).
  var PARALLEL = 3;
  var SESSION_EXPIRED = 'session expired: reload the page and sign in';

  function init(root) {
    var logBox = root.dataset.logId ? document.getElementById(root.dataset.logId) : null;
    var signUrl = root.dataset.signUrl;
    var completeUrl = root.dataset.completeUrl;
    var csrfToken = root.dataset.csrfToken;
    var purpose = root.dataset.purpose;
    var maxSize = parseInt(root.dataset.maxSize, 10) || null;
    var started = {};
    var retried = {};
    // Uppy's own (local) key for each file -> the file, so starting an upload can declare
    // the file's size and type. The site replaces the key with its own.
    var localKeys = {};

    function log(message) {
      if (!logBox) return;
      logBox.textContent += new Date().toISOString() + '  ' + message + '\n';
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
        // A redirect (the login page after the idle limit) or any non-JSON answer means
        // the session is gone, not that OVH refused something.
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
      if (!request.uploadId) return request.method === 'POST' ? 'CreateMultipartUpload' : request.method;
      return { GET: 'ListParts', POST: 'CompleteMultipartUpload', DELETE: 'AbortMultipartUpload' }[request.method]
        || request.method;
    }

    var uppy = new window.Uppy.Uppy({
      autoProceed: false,
      restrictions: { maxFileSize: maxSize, minFileSize: 1 },
    });

    uppy.use(window.Uppy.Dashboard, {
      inline: true,
      target: root,
      width: '100%',
      height: 360,
      showProgressDetails: true,
      proudlyDisplayPoweredByUppy: false,
    });

    uppy.use(window.Uppy.AwsS3, {
      shouldUseMultipart: true,
      getChunkSize: function () { return PART_SIZE; },
      limit: PARALLEL,
      // No file name or metadata goes to S3; the site makes the key.
      allowedMetaFields: false,
      generateObjectKey: function (file) {
        var local = 'local-' + crypto.randomUUID();
        localKeys[local] = file;
        return local;
      },
      signRequest: function (request) {
        var body = { method: request.method, key: request.key };
        if (request.uploadId) body.uploadId = request.uploadId;
        if (request.partNumber) body.partNumber = request.partNumber;
        if (request.method === 'POST' && !request.uploadId) {
          var file = localKeys[request.key];
          if (!file) return Promise.reject(new Error('unknown file'));
          body = { method: 'POST', purpose: purpose, size: file.size };
          if (file.type) body.contentType = file.type;
        }
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
      log('part ' + part.PartNumber + ' uploaded');
    });
    uppy.on('upload-success', function (file, response) {
      var seconds = (Date.now() - (started[file.id] || Date.now())) / 1000;
      var key = response && response.body && response.body.key;
      log('uploaded ' + file.name + ' as ' + key + ' in ' + seconds.toFixed(1) + ' s, '
          + (file.size / MiB / Math.max(seconds, 0.001)).toFixed(1) + ' MiB/s');
      postJson(completeUrl, { key: key, name: file.name }).then(function (stored) {
        log('stored ' + stored.id + ': ' + stored.size + ' bytes'
            + (stored.size === file.size ? ' (matches)' : ' (DIFFERS from local)'));
        root.dispatchEvent(new CustomEvent('storage:uploaded', { bubbles: true, detail: stored }));
      }, function (error) {
        log('recording ' + key + ' failed: ' + error.message);
        uppy.info('The upload could not be recorded: ' + error.message, 'error', 10000);
      });
    });
    uppy.on('upload-error', function (file, error) {
      log('upload of ' + (file ? file.name : '?') + ' failed: ' + (error && error.message));
      // One automatic retry for a request S3 refused with 403; it resumes the same upload
      // (ListParts), signing each request again.
      if (file && error && error.status === 403 && !retried[file.id]) {
        retried[file.id] = true;
        log('retrying ' + file.name + ' once after a 403');
        setTimeout(function () { uppy.retryUpload(file.id); }, 2000);
      }
    });
    uppy.on('upload-retry', function (file) {
      log('retry ' + (file && file.name ? file.name : ''));
    });
    uppy.on('error', function (error) { log('error: ' + (error && error.message)); });

    window.addEventListener('offline', function () { log('browser went offline'); });
    window.addEventListener('online', function () { log('browser is back online'); });
    log('ready (Uppy ' + (window.Uppy.Uppy.VERSION || '?') + ', aws-s3 '
        + (window.Uppy.AwsS3.VERSION || '?') + ')');
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll('.storage-upload'), init);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
