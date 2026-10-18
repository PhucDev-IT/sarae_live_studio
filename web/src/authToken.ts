/** Login token for the current browser tab (sessionStorage: closing the tab signs out). */
const storage = 'sarae-auth-token'

export const getAuthToken = () => sessionStorage.getItem(storage) || ''
export function setAuth(token:string){sessionStorage.setItem(storage, token);window.dispatchEvent(new Event('sarae-auth-changed'))}
export function clearAuth(){sessionStorage.removeItem(storage);window.dispatchEvent(new Event('sarae-auth-changed'))}
/** Authorization header for raw fetch calls that cannot go through apiRequest (e.g. binary responses). */
export const authHeaders = ():Record<string,string> => {const token = getAuthToken();return token ? {Authorization:'Bearer ' + token} : {}}
