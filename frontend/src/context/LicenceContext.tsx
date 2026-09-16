import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { licenceApi } from "../api/resources";
import type { Licence, LicenceFeature } from "../api/types";
import { useAuth } from "./AuthContext";

interface LicenceContextValue {
  licence: Licence | null;
  /** True until the first load finishes; pages avoid flashing an upgrade notice meanwhile. */
  loading: boolean;
  hasFeature: (feature: LicenceFeature) => boolean;
  refresh: () => Promise<void>;
}

const LicenceContext = createContext<LicenceContextValue>({
  licence: null,
  loading: true,
  hasFeature: () => true,
  refresh: async () => undefined,
});

/** Which tier the signed-in organization is on, loaded once per login and
 * after a key is applied. While unknown, every feature reads as allowed so
 * the UI never blocks a paying customer on a slow request; the API is the
 * real gate. */
export function LicenceProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [licence, setLicence] = useState<Licence | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!user) {
      setLicence(null);
      setLoading(false);
      return;
    }
    try {
      const { data } = await licenceApi.get();
      setLicence(data);
    } catch {
      setLicence(null);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  const hasFeature = useCallback((feature: LicenceFeature) => (licence ? licence.features.includes(feature) : true), [licence]);

  return <LicenceContext.Provider value={{ licence, loading, hasFeature, refresh }}>{children}</LicenceContext.Provider>;
}

export function useLicence(): LicenceContextValue {
  return useContext(LicenceContext);
}
