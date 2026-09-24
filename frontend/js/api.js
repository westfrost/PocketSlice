// Thin fetch wrapper for the PocketSlice API.
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function handle(res) {
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth-required'));
    throw new ApiError(401, 'Login required');
  }
  const ctype = res.headers.get('content-type') || '';
  const body = ctype.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = (body && (body.detail || body.error)) || (typeof body === 'string' ? body : res.statusText);
    throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  if (body && body.ok === false && body.error) throw new ApiError(502, body.error);
  return body;
}

export const api = {
  get: (url) => fetch(url, { credentials: 'same-origin' }).then(handle),
  del: (url) => fetch(url, { method: 'DELETE', credentials: 'same-origin' }).then(handle),
  post: (url, data) => fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}),
  }).then(handle),
  put: (url, data) => fetch(url, {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}),
  }).then(handle),
  // multipart upload with progress callback (XHR because fetch has no upload progress)
  upload: (url, file, onProgress) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    fd.append('file', file, file.name);
    xhr.open('POST', url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body = xhr.responseText;
      try { body = JSON.parse(body); } catch { /* text */ }
      if (xhr.status === 401) { window.dispatchEvent(new CustomEvent('auth-required')); reject(new ApiError(401, 'Login required')); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, (body && body.detail) || xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, 'Network error'));
    xhr.send(fd);
  }),
};
