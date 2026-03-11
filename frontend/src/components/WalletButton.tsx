"use client";

import { useAccount, useConnect, useDisconnect } from "wagmi";
import { injected } from "wagmi/connectors";
import { API_URL } from "@/lib/api";

interface EthereumProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}

/**
 * WalletButton — connects MetaMask / any injected wallet.
 * After connecting, prompts user to sign a message and links
 * the wallet to their AgentSpore account via PATCH /users/wallet.
 */
export function WalletButton({ authToken }: { authToken?: string }) {
  const { address, isConnected } = useAccount();
  const { connect, isPending } = useConnect();
  const { disconnect } = useDisconnect();

  const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;

  async function handleConnect() {
    connect({ connector: injected() });
  }

  async function handleLink() {
    if (!address || !authToken) return;
    const eth = window.ethereum as EthereumProvider | undefined;
    if (!eth) {
      alert("MetaMask not found. Please install the MetaMask extension.");
      return;
    }

    const message = `AgentSpore wallet link\nAddress: ${address}\nTimestamp: ${Date.now()}`;
    try {
      const accounts = await eth.request({ method: "eth_requestAccounts" }) as string[];
      const signature = await eth.request({
        method: "personal_sign",
        params: [message, accounts[0]],
      }) as string;

      const res = await fetch(`${API_URL}/api/v1/users/wallet`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ wallet_address: address, signature, message }),
      });

      if (res.ok) {
        alert("Wallet linked to your AgentSpore account!");
      } else {
        const err = await res.json();
        alert(`Failed to link: ${err.detail}`);
      }
    } catch (e) {
      alert(`Signing failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  if (!isConnected) {
    return (
      <button
        onClick={handleConnect}
        disabled={isPending}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm font-mono hover:bg-neutral-700 transition-colors disabled:opacity-50"
      >
        <span className="text-base">⟁</span>
        {isPending ? "Connecting…" : "Connect Wallet"}
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/25 text-emerald-300 text-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
        {short(address!)}
      </div>
      {authToken && (
        <button
          onClick={handleLink}
          className="px-2 py-1.5 rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-400 text-xs font-mono hover:text-white hover:bg-neutral-700 transition-colors"
        >
          Link
        </button>
      )}
      <button
        onClick={() => disconnect()}
        className="px-2 py-1.5 rounded-lg text-neutral-500 text-xs hover:text-neutral-300 transition-colors"
      >
        ✕
      </button>
    </div>
  );
}
