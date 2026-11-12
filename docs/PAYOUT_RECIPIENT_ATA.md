2026-11-12 · Issue #182, code on main from #349, #350, #351 · AgentSpore maintainers

# Payouts to wallets without a token account (shape B)

A $ASPORE payout can now go to a wallet that has no Token-2022 associated token
account (ATA) yet: the hot wallet creates that account in the same transaction and
pays its rent. The behaviour is behind the server-side flag `create_recipient_ata`,
which is off by default; with the flag off nothing changes. Each creation costs the
hot wallet about 0.0015 SOL (`getMinimumBalanceForRentExemption(170)`) that is never returned to it, so the flag is enabled only
with the signer's creation limit and a funded SOL reserve in place.

Terms used below:

- **Shape A**: one instruction, `TransferChecked` from the hot wallet's own token
  account to the recipient's existing ATA.
- **Shape B**: two instructions, ATA `CreateIdempotent` (ATA program
  `ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL`, instruction data `0x01`) paid by the
  hot wallet, then the same `TransferChecked` into the account just created.
  `CreateIdempotent` succeeds without spending lamports when the account already exists.
- **Rent**: the rent-exempt minimum for a 170-byte Token-2022 account (165 bytes of
  token account, the account-type byte and the `immutableOwner` extension).

| When | Flag `create_recipient_ata` | Effect |
|---|---|---|
| `welcome-payouts link` | off (default) | Recipient's ATA must exist and be usable, else `no_token_account` |
| `welcome-payouts link` | on | Missing ATA is accepted; see the link verdicts below |
| `auto-compile` | off | Shape A |
| `auto-compile` | on | SOL check, then shape B for every new compile |
| `prepare-message` (manual) | either | Always shape A |
| receipt check, reconcile | either | Shape picked from the stored transaction, A or B |

## Configuration

`create_recipient_ata` is an optional boolean in the private network config file
named by `PAYOUT_NETWORK_CONFIG_PATH` (`NetworkConfig` in
`backend/app/schemas/payout_delivery.py`). A missing key means `false`. Every new
`auto-compile` reads it; already signed and stored transactions are never rebuilt, so
turning the flag off affects only future compiles and an already signed shape-B
transaction is still sent and reconciled as it is.

Manual `prepare-message` never reads the flag and always builds shape A
(`backend/app/cli/payout_delivery.py`, `dispatch_command`). Use it only for
recipients whose ATA already exists.

## Compile (`backend/app/services/payout_transaction.py`)

`build_payout_message` with `create_ata` inserts the `CreateIdempotent` instruction
before the transfer. Its accounts are, in order: fee payer (signer, writable), the
recipient's ATA (writable), the recipient wallet, the mint, the System program and the
Token-2022 program. Before building, the destination must equal the ATA derived from
the recipient wallet, the mint and Token-2022, and the wallet must be on the ed25519
curve; otherwise the compile is rejected. `TransferIntent` additionally refuses a
recipient equal to the authority, fee payer, source account, mint, Token-2022, the ATA
program or the System program. `bind_signed_candidate` picks shape A or B from the
instruction count of the stored message (1 or 2), so wires signed before the flag
changed still bind.

## Link verdicts (`backend/app/services/welcome_payout_link_service.py`)

With the flag on, `link` reads the wallet and its derived ATA in one finalized
`getMultipleAccounts` call (`ata_creation_verdict`):

| Verdict | Meaning | Operator action |
|---|---|---|
| (linked) | ATA absent, a plain System account with no data, or a usable token account | None |
| `invalid_wallet` | Wallet is off the ed25519 curve, or exists but is not a System-owned account with empty data | Ask the owner for an ordinary wallet address |
| `bad_token_account` | Something sits at the ATA address that is not a usable account | See below |
| `rpc_error` | The lookup failed or returned a malformed answer | Nothing; the next run retries |

A usable token account (`usable_token_account`) is an initialized Token-2022 account of
the program mint owned by the wallet, whose extensions pass `allowed_extensions`: only
`immutableOwner`, and `memoTransfer` with required incoming memos switched off. A
frozen account, another mint, another owner or program, or any other extension gives
`bad_token_account`. The grant is skipped on every run until that changes; nothing is
paid. The usual fix is on the owner's side (turn off required memos, or save another
wallet); the operator only tells them. With the flag off, the old rule applies: any
missing or unusable account is `no_token_account`.

## SOL check before compile (`backend/app/cli/payout_delivery.py`)

With the flag on, `auto-compile` reads the signer's finalized balance (`getBalance`)
and the rent-exempt minimum for 170 bytes (`getMinimumBalanceForRentExemption`) before
compiling (`signer_can_fund_ata`). The balance must cover one rent, one signature fee
and the reserve `SIGNER_RESERVE_LAMPORTS`; otherwise the verdict is
`insufficient_sol` (exit code 15), nothing is compiled or signed and the signer ledger
is untouched. An RPC failure in this check is `unresolved`. The orchestrator turns any
non-`compiled` verdict into the alert `compile_<verdict>` (here
`compile_insufficient_sol`) and releases the claim, so the intent is retried on the next tick. Operator action: top up SOL on
`<hot-wallet>`, or turn the flag off.

## SOL budget inside one tick

The check covers one creation against a finalized balance. Several intents compiled
in the same tick, or before earlier sends finalize, all see the same balance, so their
rents are not counted against each other. Rule: `per_tick` (orchestrator config) times
the rent must stay below the reserve, so that the reserve, not the fees, absorbs a full
tick of creations. When it does not hold, lower `per_tick`; the reserve is a code
constant, not a setting.

The signer bounds the daily spend independently with `max_ata_creations_24h`
([payout-signer](../ops/payout-signer/README.md)); it counts every shape-B signature as
a creation, because it cannot see whether the account exists. The worst-case daily rent
is that limit times the rent.

## Receipt reconciliation (`backend/app/services/payout_delivery_service.py`)

`receipt_verdict` treats a transaction with two instructions as shape B and accepts it
as `matching` only when all of the following hold:

- the instructions are exactly `CreateIdempotent` then `TransferChecked`, with the
  accounts and data the compiler produces;
- the account keys are those of shape A plus the recipient wallet, the System program
  and the ATA program, all read-only and unsigned;
- inner instructions (calls made by the ATA program) come only from the System program
  or Token-2022; none at all is accepted (the account already existed);
- lamports change only on the fee payer (minus the fee and the rent) and on the new
  account (plus the same rent, zero or more);
- a missing pre-transfer token balance of the destination counts as 0, and the
  post-transfer balance shows the recipient wallet as owner, the intent's mint, program
  and decimals, with the exact raw debit and credit.

Anything else, including inner instructions in shape A, is `unsupported`. The rules of
shape A are unchanged ([PAYOUT_RECEIPTS.md](PAYOUT_RECEIPTS.md)).

## Enabling

Strictly in this order:

1. Backend release that contains the shape-B code, flag absent: behaviour unchanged.
2. Disable the orchestrator timer, pause the orchestrator and the signer, and wait until
   `systemctl is-active payout-orchestrator.service` is `inactive` (no tick in flight).
   Replace the signer with one that knows
   `max_ata_creations_24h` and add that key with `<N>` in the same window
   ([upgrade notes](../ops/payout-signer/README.md#upgrade-notes));
   `payout-signer status` must show `ata_creations`.
3. Fund `<hot-wallet>` with SOL for the creations you allow plus fees and the reserve;
   check that `per_tick` times the rent stays below the reserve.
4. Set `"create_recipient_ata": true` in the network config file. No restart is needed:
   every backend CLI call is a new process that reads the file again.
5. Canary: a resumed tick links every eligible grant and pays up to `per_tick` of them,
   so set `per_tick = 1`, resume, run one tick by hand
   (`systemctl start payout-orchestrator.service`) and confirm `auto-reconcile` answers
   `confirmed` for a wallet that had no account. Then restore `per_tick` and enable the
   timer.

Rollback: set the flag to `false` (or remove the key); the next compile builds shape A,
and an already signed shape-B transaction is still sent and reconciled. Caution: a grant
linked while the flag was on whose wallet still has no account then compiles as shape A,
fails the send preflight and pauses the orchestrator. Before turning the flag off, check
that no such grant is waiting. Signer kill switch: `max_ata_creations_24h = 0`.
