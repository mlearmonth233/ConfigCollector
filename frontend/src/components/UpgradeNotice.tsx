import { Link } from "react-router-dom";

import type { LicenceFeature } from "../api/types";
import { useLicence } from "../context/LicenceContext";

/** Shown in place of a paid feature's controls on the free tier. Renders
 * nothing while the licence is still loading or when the feature is
 * unlocked, so pages can drop it in unconditionally. */
export function UpgradeNotice({ feature, children }: { feature: LicenceFeature; children?: React.ReactNode }) {
  const { licence, loading, hasFeature } = useLicence();
  if (loading || hasFeature(feature) || !licence) return null;
  const label = licence.all_features[feature] ?? feature;
  return (
    <div className="upgrade-notice" role="note">
      <strong>{label}</strong> is part of Colony and Warren. This organization is on {licence.tier_label}
      {licence.problem ? ` (${licence.problem})` : ""}.{" "}
      {children ?? "Everything you set up here is kept, but nothing runs until a licence key is applied."}{" "}
      <Link to="/settings#licence">Enter a licence key</Link>.
    </div>
  );
}
