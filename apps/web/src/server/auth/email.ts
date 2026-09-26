/**
 * What the development sign-in accepts as an address.
 *
 * Normalised the way the API's seam normalises the header it receives
 * (`_claimed_email` in services/api/app/api/dev_identity.py: stripped and
 * lower-cased), so the address in the session is the one the API resolves and
 * the header shows. Checked more strictly than the API checks — the API only
 * refuses what would break its column; a sign-in form can also refuse what is
 * plainly not an address.
 */

/** users.email is VARCHAR(255); the API refuses anything longer. */
export const EMAIL_MAX_LENGTH = 255;

export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

export function isUsableEmail(value: string): boolean {
  if (value.length === 0 || Array.from(value).length > EMAIL_MAX_LENGTH) return false;
  // No whitespace and no control characters anywhere: the value becomes an
  // HTTP header on the way to the API.
  if (/[\s\u0000-\u001f\u007f]/.test(value)) return false;
  const at = value.indexOf("@");
  return at > 0 && at === value.lastIndexOf("@") && at < value.length - 1;
}
