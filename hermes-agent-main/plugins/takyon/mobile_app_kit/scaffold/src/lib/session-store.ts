import * as SecureStore from "expo-secure-store";

// The single app-session credential — the mobile equivalent of the web APP_SESSION cookie, kept in
// the iOS Keychain (no Required-Reason API; that's why the privacy manifest declares only
// UserDefaults/AsyncStorage, not Keychain).
const KEY = "takyon_app_session";

export const getToken = () => SecureStore.getItemAsync(KEY).then((v) => v ?? "");
export const setToken = (t: string) =>
  SecureStore.setItemAsync(KEY, t, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
export const clearToken = () => SecureStore.deleteItemAsync(KEY);
