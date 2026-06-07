/* SCREEN: Auth modal (overlay) · auth: public
   Sign up / Log in. hooks: Auth0 (social + email) → on success set session → "/".
   In this mock the buttons call onAuthed() (mock login). */
import { BulbMark } from "../shared/icons";
import "./authmodal.css";

export type AuthMode = "login" | "signup";

export function AuthModal({
  mode, onClose, onAuthed, onSwitch,
}: {
  mode: AuthMode;
  onClose: () => void;
  onAuthed: () => void;
  onSwitch: () => void;
}) {
  const isSignup = mode === "signup";
  return (
    <div className="lb-authwrap" role="dialog" aria-modal="true" aria-label={isSignup ? "Sign up" : "Log in"}>
      <div className="lb-authscrim" onClick={onClose} />
      <div className="lb-auth">
        <button className="lb-auth__x" onClick={onClose} aria-label="Close">×</button>
        <span className="lb-auth__brand"><BulbMark size={32} tone="brand" /></span>
        <h2 className="lb-auth__title">{isSignup ? "Create your account" : "Welcome back"}</h2>
        <p className="lb-auth__sub">{isSignup ? "Start building your company in minutes." : "Log in to your companies."}</p>

        <button className="lb-auth__oauth" onClick={onAuthed}>
          <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden="true"><path fill="#4285F4" d="M17.6 9.2c0-.6-.1-1.2-.2-1.7H9v3.4h4.8a4.1 4.1 0 0 1-1.8 2.7v2.2h2.9c1.7-1.6 2.7-3.9 2.7-6.6z"/><path fill="#34A853" d="M9 18c2.4 0 4.5-.8 6-2.2l-2.9-2.2c-.8.5-1.8.9-3.1.9-2.4 0-4.4-1.6-5.1-3.8H.8v2.300A9 9 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.9 10.7a5.4 5.4 0 0 1 0-3.4V5H.8a9 9 0 0 0 0 8z"/><path fill="#EA4335" d="M9 3.6c1.3 0 2.5.5 3.4 1.3l2.6-2.6A9 9 0 0 0 .8 5l3.1 2.4C4.6 5.2 6.6 3.6 9 3.6z"/></svg>
          Continue with Google
        </button>

        <div className="lb-auth__or"><span>or</span></div>

        <label className="lb-auth__field">
          <span>Email</span>
          <input type="email" inputMode="email" autoComplete="email" placeholder="you@email.com" />
        </label>
        <button className="b44-btn b44-btn--brand lb-auth__submit" onClick={onAuthed}>
          {isSignup ? "Sign up with email" : "Log in with email"}
        </button>

        <p className="lb-auth__switch">
          {isSignup ? "Already have an account?" : "New to Litebulb?"}{" "}
          <button onClick={onSwitch}>{isSignup ? "Log in" : "Sign up"}</button>
        </p>
        <p className="lb-auth__legal">Secured by Auth0 · by continuing you agree to the Terms.</p>
      </div>
    </div>
  );
}
