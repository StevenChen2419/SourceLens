import { describe, expect, it } from 'vitest';
import { validateProductionApiUrl } from './buildConfig';

describe('production API configuration', () => {
  it('accepts and normalizes the configured HTTPS backend', () => {
    expect(validateProductionApiUrl('https://api.example.com/')).toBe('https://api.example.com');
  });
  it.each([undefined, '', ' ', 'not a URL', 'http://api.example.com',
    'https://localhost', 'https://127.0.0.1', 'https://[::1]',
    'https://user:password@api.example.com', 'https://api.example.com/api',
    'https://api.example.com?key=value', 'https://api.example.com#fragment',
  ])('rejects missing or unsafe production URL %s', (value) => {
    expect(() => validateProductionApiUrl(value)).toThrow(/VITE_API_BASE_URL/);
  });
});
