"use client";

import { useAccount, useConnect, useDisconnect } from "wagmi";
import { injected } from "wagmi/connectors";
import { API_URL } from "@/lib/api";

interface EthereumProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
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
    if (!window.ethereum) {
      alert("MetaMask not found. Please install the MetaMask extension.");
      return;
    }

    const message = `AgentSpore wallet link\nAddress: ${address}\nTimestamp: ${Date.now()}`;
    try {
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" }) as string[];
      const signature = await window.ethereum.request({
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
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-violet-500/15 border border-violet-500/30 text-violet-300 text-sm hover:bg-violet-500/25 transition-colors disabled:opacity-50"
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
          className="px-2 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 text-xs hover:text-white hover:bg-white/10 transition-colors"
        >
          Link
        </button>
      )}
      <button
        onClick={() => disconnect()}
        className="px-2 py-1.5 rounded-lg text-slate-500 text-xs hover:text-slate-300 transition-colors"
      >
        ✕
      </button>
    </div>
  );
}
