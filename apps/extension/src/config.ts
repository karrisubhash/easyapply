/**
 * Build-time configuration. Read only by the three entry points; every other
 * module takes what it needs as an argument, which is what lets the tests run
 * it against any address.
 */

declare const __EASYAPPLY_WEB_URL__: string;

/**
 * The EasyApply web app this build saves to — its origin, no path.
 *
 * Fixed when the extension is built (`EASYAPPLY_WEB_URL`, default
 * http://localhost:3000) rather than a setting in the popup: its origin is also
 * the one host permission this extension holds besides the job sites, and a
 * runtime setting would need a permission for any origin it could be set to.
 */
export const WEB_URL: string = __EASYAPPLY_WEB_URL__;
