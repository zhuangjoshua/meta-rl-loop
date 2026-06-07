/* SCREEN: 404 / error · route: * · auth: public */
import { BulbMark } from "../shared/icons";
import "./notfound.css";

export function NotFound({ onHome }: { onHome: () => void }) {
  return (
    <div className="lb-404">
      <BulbMark size={40} tone="brand" />
      <div className="lb-404__code">404</div>
      <p className="lb-404__msg">That page wandered off. Let's get you back.</p>
      <button className="b44-btn b44-btn--brand" onClick={onHome}>Back to Litebulb</button>
    </div>
  );
}
