import { createConfig, createStorage, http } from "wagmi";
import { base } from "wagmi/chains";
import { injected } from "wagmi/connectors";

export const wagmiConfig = createConfig({
  chains: [base],
  transports: {
    [base.id]: http("https://mainnet.base.org"),
  },
  connectors: [injected()],
  ssr: true,
  storage: createStorage({
    storage: {
      getItem(key) {
        try { return window.localStorage.getItem(key); }
        catch { return null; }
      },
      setItem(key, value) {
        try { window.localStorage.setItem(key, value); }
        catch { return; }
      },
      removeItem(key) {
        try { window.localStorage.removeItem(key); }
        catch { return; }
      },
    },
  }),
});
