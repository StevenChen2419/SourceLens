/** Validate build-time public configuration; never put credentials in VITE_ values. */
export function validateProductionApiUrl(value: string | undefined): string {
  if (!value?.trim()) {
    throw new Error('VITE_API_BASE_URL is required for a production build. Set the backend HTTPS origin.');
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error('VITE_API_BASE_URL must be a valid HTTPS backend origin.');
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || (url.pathname !== '/' && url.pathname !== '')
      || url.hostname === 'localhost' || url.hostname.endsWith('.localhost')
      || url.hostname.startsWith('127.') || url.hostname === '[::1]') {
    throw new Error('VITE_API_BASE_URL must be an HTTPS backend origin without credentials, paths, or localhost.');
  }
  return url.origin;
}
