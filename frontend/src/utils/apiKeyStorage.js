/**
 * Utility for managing API key storage in localStorage with expiration.
 */

const STORAGE_KEY = "llm_council_api_key";
const EXPIRY_KEY = "llm_council_api_key_expiry";
const EXPIRY_DAYS = 7;

export const apiKeyStorage = {
	/**
	 * Get the stored API key if valid and not expired.
	 * @returns {string|null} The API key or null if not set/expired
	 */
	get() {
		const expiry = localStorage.getItem(EXPIRY_KEY);
		if (expiry && Date.now() > parseInt(expiry, 10)) {
			this.clear();
			return null;
		}
		return localStorage.getItem(STORAGE_KEY);
	},

	/**
	 * Store an API key with expiration.
	 * @param {string} key - The API key to store
	 */
	set(key) {
		const expiryTime = Date.now() + EXPIRY_DAYS * 24 * 60 * 60 * 1000;
		localStorage.setItem(STORAGE_KEY, key);
		localStorage.setItem(EXPIRY_KEY, expiryTime.toString());
	},

	/**
	 * Clear the stored API key and expiry.
	 */
	clear() {
		localStorage.removeItem(STORAGE_KEY);
		localStorage.removeItem(EXPIRY_KEY);
	},

	/**
	 * Get the expiry date.
	 * @returns {Date|null} The expiry date or null if not set
	 */
	getExpiry() {
		const expiry = localStorage.getItem(EXPIRY_KEY);
		return expiry ? new Date(parseInt(expiry, 10)) : null;
	},

	/**
	 * Check if the stored key is expired.
	 * @returns {boolean} True if expired or not set
	 */
	isExpired() {
		const expiry = localStorage.getItem(EXPIRY_KEY);
		return expiry ? Date.now() > parseInt(expiry, 10) : true;
	},
};
