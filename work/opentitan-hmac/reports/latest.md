# Spec and test gaps: hmac

Repository `targets/opentitan` at commit `b84154c71810`. Report generated 2026-11-10 21:12 UTC by chipbag.

Each finding below gives the gap, the evidence for it as exact quotes at their file and line, and how it was verified: an agent searched the repository to refute the finding, and the finding was kept only if that failed. Every quote was checked against the file at the cited lines. The report does not recommend a fix.

## Summary

| # | Category | Verified | Withdrawn | Inconclusive |
|---|---|---:|---:|---:|
| 1 | [Spec too ambiguous to test](#1-spec-too-ambiguous-to-test) | 10 | 8 | 0 |
| 2 | [Spec contradicts itself](#2-spec-contradicts-itself) | 1 | 1 | 0 |
| 3 | [Test contradicts its own description](#3-test-contradicts-its-own-description) | 2 | 5 | 0 |
| 4 | [Spec and test disagree](#4-spec-and-test-disagree) | 0 | 0 | 0 |
| 5 | [Spec not tested](#5-spec-not-tested) | 28 (89 items) | 17 | 4 |

2 verified findings with the same subject as another are shown together with it and counted once.

## Scope

**Spec sources (5):** `hw/ip/hmac/README.md`, `hw/ip/hmac/data/hmac.hjson`, `hw/ip/hmac/doc/interfaces.md`, `hw/ip/hmac/doc/programmers_guide.md`, `hw/ip/hmac/doc/theory_of_operation.md`

**Verification sources (54 files):** `hw/ip/hmac/dv/env/seq_lib/` (14), `sw/device/tests/` (8), `hw/dv/tools/dvsim/testplans/` (5), `hw/ip/hmac/dv/env/` (5), `hw/dv/sv/test_vectors/vectors/hmac/` (4), `hw/ip/hmac/data/` (2), `hw/ip/hmac/dv/` (2), `hw/ip/hmac/dv/cov/` (2), `hw/top_earlgrey/dv/` (2), `sw/device/tests/crypto/` (2), `hw/ip/hmac/doc/` (1), `hw/ip/hmac/dv/cryptoc_dpi/` (1), `hw/ip/hmac/dv/sva/` (1), `hw/ip/hmac/dv/tests/` (1), `hw/ip/hmac/model/` (1), `hw/top_earlgrey/data/ip/` (1), `hw/top_earlgrey/dv/env/seq_lib/` (1), `sw/device/lib/dif/` (1). The full list is in the manifest.

411 requirements after merging duplicates; 338 need a test (the rest hold by construction). 88 tests (68 with source), 65 test plan items. 210 requirements are matched to a test.

Files the verifier read that are not in the manifest: `hw/dv/sv/cip_lib/seq_lib/cip_base_vseq__tl_errors.svh`, `hw/dv/sv/csr_utils/csr_hw_reset_seq.sv`, `hw/dv/tools/dvsim/testplans/csr_testplan.hjson`, `hw/dv/tools/dvsim/tests/csr_tests.hjson`, `hw/dv/tools/dvsim/tests/tl_access_tests.hjson`, `hw/ip/hmac/README.md`, `hw/ip/hmac/data/hmac.hjson`, `hw/ip/hmac/doc/programmers_guide.md`, `hw/ip/hmac/doc/registers.md`, `hw/ip/hmac/dv/env/hmac_scoreboard.sv`, `hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv`, `hw/ip/hmac/dv/sva/hmac_bind.sv`, `hw/ip/prim/doc/prim_packer.md`, `sw/device/tests/hmac_enc_test.c`, `sw/device/tests/keymgr_dpe_sideload_hmac_test.c`.

## 1. Spec too ambiguous to test

### 1.1 The module can wipe internal variables such as the secret key, intermediate hash results, digest and message scheduling array.

`f_08abef58a364` · vague · severity medium · section: hw/ip/hmac/README.md > Overview > Description

**Gap:** The 'such as' list does not state exactly which internal variables are wiped.

**Spec says:**

- [`hw/ip/hmac/README.md:59`][1]: `is able to wipe internal variables such as the secret key, intermediate hash results, digest and the internal message scheduling array.`

**Not answered by the spec:** Which internal state exactly does a WIPE_SECRET write overwrite with the (replicated) written value? Specifically: key registers KEY_0-31, digest/intermediate hash registers H0-H7, the message schedule array W, the message length counter, and any partially accumulated block. For each, after a wipe, what value does SW observe (e.g. DIGEST_n == wipe value), and what digest results if hashing continues without reprogramming the key?

**Verified:** The README, the register description and the hjson all repeat the same "such as" list. They add that the internal variables are overwritten with the written value and that the message FIFO is not wiped. None of them say which state is wiped (key registers, H0-H7, W, length counter, partial block), what SW reads back, or what digest results if hashing continues without reprogramming the key.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/data/hmac.hjson:454-456`][2]: `the value is used to clear some internal variables such as the secret key, intermediate hash results, digest and internal message scheduling array.` — The register description uses the same non-exhaustive 'such as' wording.

**Searched:**

- WIPE_SECRET descriptions in the HMAC docs, register docs and hjson in `hw/ip/hmac`: README.md, doc/registers.md and data/hmac.hjson all give the same vague wording. None gives an exhaustive list or says what SW reads back.

**Looked at and ruled out:**

- [`hw/ip/hmac/README.md:60`][3]: It says the message FIFO is not wiped, but it does not address the key registers, the digest, the length counter or the post-wipe results.

---

### 1.2 The position of these zeros depends on the endianness, thus on the programmed CFG.key_swap.

`f_2ab2710b6d0c` · missing detail · severity medium · section: hw/ip/hmac/data/hmac.hjson > hmac > CFG

**Gap:** The position of the padding zeros for each key_swap value is not stated.

**Spec says:**

- [`hw/ip/hmac/data/hmac.hjson:311`][4]: `The position of these zeros depends on the endianness, thus on the programmed CFG.key_swap.`

**Not answered by the spec:** For a key shorter than the programmed key_length, at which byte positions of the KEY_* registers (and of the last partial word) must the zero padding sit when key_swap=0 and when key_swap=1?

**Verified:** Other HMAC docs only repeat the same sentence. None says where the padding zeros sit in the KEY_* registers or the last partial word for key_swap=0 versus 1. The finding stands.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/doc/theory_of_operation.md:125`][5]: ``The position of these zeros depends on the endianness, thus on the programmed [`CFG.key_swap`](registers.md#cfg--key_swap).`` — Repeats the same statement with no byte positions.
- [`hw/ip/hmac/doc/registers.md:234`][6]: `If 1 the endianness of each KEY_* register is swapped. Default value (value 0) is big endian representation of the KEY_* CSRs.` — Describes the swap only; gives no padding position.

**Searched:**

- key_swap, padding and zero-pad in the HMAC docs in `hw/ip/hmac (*.md)`: Only the same sentence repeated in registers.md and theory_of_operation.md. Other padding mentions concern SHA-2 message padding and ipad/opad.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:123`][7]: Says the key registers are big-endian by default and can be swapped, but does not say where the zero padding sits.

**Same subject, also found as spec too ambiguous to test** (`f_7cebd576a1f3`, missing detail): The zero position for each CFG.key_swap value is not specified.

**Verified:** The same sentence appears in registers.md (CFG.key_length) and in hmac.hjson. No document gives the KEY_n word or byte positions of the zero padding for either key_swap value. The only other text says key_swap swaps the endianness of each KEY_* register and that the default is big endian. That does not say which KEY words hold the key bits and which hold zeros.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/doc/registers.md:197-198`][8]: ``The position of these zeros depends on the endianness, thus on the programmed [`CFG.key_swap`](registers.md#cfg--key_swap).`` — Repeats the same vague sentence in the register description. The 80-bit example gives no register layout.

---

### 1.3 they are irrelevant and should not be read out.

`f_1f0bad59c809` · weak modal · severity medium · section: hw/ip/hmac/data/hmac.hjson > hmac > DIGEST

**Gap:** 'should not be read out' does not say what a read returns or whether it is prohibited.

**Spec says:**

- [`hw/ip/hmac/data/hmac.hjson:504`][9]: `they are irrelevant and should not be read out.`

**Not answered by the spec:** In SHA-2 384 mode, what value does a read of DIGEST12-DIGEST15 return (zero, the truncated internal state words, or undefined), and does the read complete without a bus error?

**Verified:** No spec text defines what a read of DIGEST12-15 returns in SHA-384 mode. It also doesn't say whether the read causes a bus error. The register docs repeat the same wording. The programmer's guide only says the registers are not to be read and hold irrelevant values.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/doc/programmers_guide.md:44`][10]: ``For SHA-2 384, only `DIGEST_0`..`11` should be read out, the rest should be truncated out by not being read by the SW.`` — Only says software should not read them. It defines no value and no error.

**Searched:**

- irrelevant, truncat, DIGEST12-15 wording in `hw/ip/hmac docs and hjson`: Only the same wording in registers.md, programmers_guide.md, theory_of_operation.md and hmac.hjson. None defines a read value or whether the read returns an error.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/programmers_guide.md:43`][11]: Says the unused registers 'would hold irrelevant values'. That is still undefined, so it does not say what a read returns.

---

### 1.4 and {DIGEST8 - DIGEST15} are irrelevant and should not be read out.

`f_c9d86b452dad` · weak modal · severity medium · section: hw/ip/hmac/data/hmac.hjson > hmac > DIGEST

**Gap:** 'should not be read out' does not say what a read returns or whether it is prohibited.

**Spec says:**

- [`hw/ip/hmac/data/hmac.hjson:503`][12]: `and {DIGEST8 - DIGEST15} are irrelevant and should not be read out.`

**Not answered by the spec:** In SHA-2 256 mode, what value does a read of DIGEST8-DIGEST15 return (zero, stale value, or undefined), and does the read complete without a bus error?

**Verified:** No other spec text says what a read of DIGEST8-15 returns in SHA-256 mode or whether it errors. The programmer's guide only says the registers "would hold irrelevant values" and should not be read. Zero is specified only while an operation is in progress, which is a different case.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/doc/programmers_guide.md:43`][11]: ``For SHA-2 256, only `DIGEST_0`..`7` should be read out, the remaining digest registers are redundant and would hold irrelevant values.`` — Still says 'irrelevant values' and gives no read value or error behaviour.

**Searched:**

- DIGEST and irrelevant across hmac md/hjson docs in `hw/ip/hmac`: Only the 'irrelevant / should not be read out' wording plus the in-progress-returns-zero statements; nothing on stale or zero value for DIGEST8-15 after SHA-256 completion, and nothing on bus errors.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:161`][13]: Covers the zero-read only during a sideloaded-key operation, not the idle SHA-256 case.

---

### 1.5 The lower 3 bits [2:0] of the message length are ignored.

`f_dfa447ca7429` · unclear referent · severity medium · section: hw/ip/hmac/data/hmac.hjson > hmac > MSG_LENGTH_LOWER

**Gap:** It is unclear whether the ignored bits are ignored on software writes, in the hardware length computation, or in the value read back.

**Spec says:**

- [`hw/ip/hmac/data/hmac.hjson:529`][14]: `Lower 3 bits [2:0] are ignored.`

**Not answered by the spec:** When software writes MSG_LENGTH_LOWER with bits [2:0] nonzero, does readback return those bits or zero? Are bits [2:0] always zero in the hardware-updated value, and are they excluded when the length is used in the padding computation?

**Verified:** No other spec text says whether bits [2:0] of MSG_LENGTH_LOWER read back as written or as zero. Nothing says whether hardware-updated values always have them clear, or whether the padding computation excludes them. The only related text is "Message is byte granularity", which implies the bits carry no information but doesn't settle the readback behavior.

**Searched:**

- message length, msg_length, MSG_LENGTH in the HMAC markdown docs in `hw/ip/hmac/**/*.md`: registers.md only repeats the hjson text. theory_of_operation.md covers padding and the length in general terms but says nothing about bits [2:0]. programmers_guide.md covers context save and restore without mentioning the ignored bits.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:59-77`][15]: Describes padding and length computation generally. It doesn't say what happens to bits [2:0] on write or readback.

---

### 1.6 hmac_idle indicates HMAC idle status.

`f_2f0738731f9b` · missing detail · severity medium · section: hw/ip/hmac/data/hmac.hjson > hmac > STATUS

**Gap:** The conditions that define the HMAC as idle are not stated.

**Spec says:**

- [`hw/ip/hmac/data/hmac.hjson:417`][16]: `HMAC idle status.`

**Not answered by the spec:** What is the exact set of conditions under which STATUS.hmac_idle reads 1? Specifically: is it 0 while the message FIFO holds data before hash_start/hash_process? Is it 0 from hash_stop until the stop completes? Which events cause the 0-to-1 transition, and at what latency relative to the hmac_done interrupt?

**Verified:** No spec text defines the exact conditions under which hmac_idle reads 1. The docs only say writes to DIGEST and MSG_LENGTH are allowed when idle, and that polling hmac_idle can replace waiting for hmac_done after hash_process. Neither answers the FIFO, hash_stop or latency questions.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/data/hmac.hjson:418`][17]: ``When IDLE, the `DIGEST` and the `MSG_LENGTH_LOWER`/`MSG_LENGTH_UPPER` can be written to from SW`` — Only states the consequence of idle, not its definition.

**Searched:**

- idle mentions in hmac docs and hjson in `hw/ip/hmac`: Only the register description, the programmer's guide polling note, and a sideload-abort note that says the core returns to idle. No definition of idle conditions.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:156`][18]: Says the core returns to idle after a sideload-key abort. It doesn't define hmac_idle for the FIFO, hash_stop or latency cases.

---

### 1.7 For SHA-2 256, only DIGEST_0..7 should be read out, the remaining digest registers are redundant and would hold irrelevant values.

`f_8fed7e244a4e` · weak modal · severity medium · section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Triggering HMAC/SHA-2 engine

**Gap:** 'should be read out' and 'irrelevant values' do not state a testable requirement on the hardware.

**Spec says:**

- [`hw/ip/hmac/doc/programmers_guide.md:43`][11]: `For SHA-2 256, only DIGEST_0..7 should be read out, the remaining digest registers are redundant and would hold irrelevant values.`

**Not answered by the spec:** In SHA-2 256 mode, what value must DIGEST_8..15 read back after a hash completes (zero, unchanged/stale, or undefined and excluded from checking)? Does this also hold after switching from a SHA-2 384/512 operation?

**Verified:** No other spec text defines the value of DIGEST_8..15 in SHA-256 mode. The register description repeats that they are "irrelevant and should not be read out", which leaves the value undefined. It doesn't say what they hold after a prior SHA-512 operation.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/data/hmac.hjson:503`][12]: `{DIGEST8 - DIGEST15} are irrelevant and should not be read out.` — Register description: says the registers are irrelevant but gives no value.

**Searched:**

- DIGEST_8, irrelevant, redundant in hmac md and hjson files in `hw/ip/hmac`: Only the same 'irrelevant / should not be read out' wording. No value is specified.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/registers.md:422-423`][19]: This is generated from the hjson and repeats the 'irrelevant' wording. It doesn't define a value.

---

### 1.8 Starting an operation while the sideloaded key is invalid is blocked.

`f_fc00646d2273` · missing detail · severity medium · section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

**Gap:** 'Blocked' does not say how the start command is handled.

**Spec says:**

- [`hw/ip/hmac/doc/programmers_guide.md:110`][20]: `Starting while the key is invalid is blocked`

**Not answered by the spec:** When CMD.hash_start is written while the sideloaded key is invalid, is the command dropped with the engine left fully idle? Specifically: - Are subsequent MSG_FIFO writes discarded or accepted, and is MSG_LENGTH updated? - Is a following CMD.hash_process ignored, or does it also report an error? - Are DIGEST_0-15 left unchanged or cleared? - Is the only side effect ERR_CODE=SwInvalidConfig plus INTR_STATE.hmac_err?

**Verified:** No spec text defines what a blocked start does beyond the error. Other docs only repeat that start is blocked and ERR_CODE reports SwInvalidConfig. The details of the abort case (digest cleared, FIFO cleared, no done) are specified only for a key revoked mid-operation, not for a blocked start. Nothing covers MSG_FIFO writes, MSG_LENGTH, a following hash_process, or DIGEST contents after a blocked start.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/doc/theory_of_operation.md:154`][21]: ``Triggering `CMD.hash_start` or `CMD.hash_continue` while the key is invalid is blocked, and [`ERR_CODE`](registers.md#err_code) reports `SwInvalidConfig`.`` — Same vague wording; no state details.
- [`hw/ip/hmac/doc/programmers_guide.md:123`][22]: `Starting while it is still invalid is blocked with the same error.` — Again no behavior beyond the error.

**Searched:**

- SwInvalidConfig / invalid key / blocked wording in `hw/ip/hmac docs and hjson`: Only the same 'blocked + error' statements in the programmer's guide, theory of operation, registers.md Key_None, and the error table.
- error and recovery text in the programmer's guide in `hw/ip/hmac/doc/programmers_guide.md`: Recovery steps cover the abort case only; nothing on MSG_FIFO, process or DIGEST after a blocked start.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:155-157`][23]: Describes the key revoked during an operation, not a blocked start.

---

### 1.9 When a sideloaded key is used, CMD.hash_stop and CMD.hash_continue are rejected (saving and restoring the context is not available).

`f_099bc6608b8a` · missing detail · severity medium · section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading > Restrictions with a sideloaded key

**Gap:** It is not stated what 'rejected' means for the command (ignored, state unchanged, etc.).

**Spec says:**

- [`hw/ip/hmac/doc/programmers_guide.md:102`][24]: `Saving and restoring the context is not available`
- [`hw/ip/hmac/doc/programmers_guide.md:102`][24]: `CMD.hash_stop and CMD.hash_continue are rejected`
- [`hw/ip/hmac/doc/theory_of_operation.md:164`][25]: ``saving and restoring the context is not available with a sideloaded key: `CMD.hash_stop` and `CMD.hash_continue` are rejected``

**Not answered by the spec:** When CMD.hash_stop or CMD.hash_continue is rejected with a sideloaded key, does the in-progress operation continue unaffected and produce the correct final digest after hash_process? Or is it aborted, and if so what state do the FSM, DIGEST, MSG_LENGTH and the done interrupt end up in? Is the rejected command simply dropped with no other side effect besides ERR_CODE=SwInvalidConfig and the hmac_err interrupt?

**Verified:** No other spec text defines what "rejected" means for hash_stop/hash_continue with a sideloaded key. The docs only say the commands are rejected and ERR_CODE reports SwInvalidConfig. The key-invalid case is spelled out in detail, including the abort, the cleared state and no hmac_done. Nothing equivalent exists for rejected stop/continue.

**Searched:**

- hash_stop/hash_continue in hmac docs and hjson in `hw/ip/hmac (md, hjson)`: Only the theory_of_operation and programmers_guide lines (plus the generic hjson/registers.md CMD descriptions) mention these; none defines the reject behaviour.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:152-157`][26]: Describes blocked start/continue with an invalid key and the abort on key invalidation, not the rejection of stop/continue with a valid sideloaded key.

---

### 1.10 Revocation of the key by the key manager takes effect immediately.

`f_b7de457c4c27` · missing detail · severity low · section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

**Gap:** "Immediately" gives no bound on how many cycles it takes for revocation to affect the HMAC core.

**Spec says:**

- [`hw/ip/hmac/doc/theory_of_operation.md:139`][27]: `the key manager revoking the key takes effect immediately`

**Not answered by the spec:** When the key manager deasserts the sideload valid indication during an operation, within how many clock cycles must the HMAC core abort, set ERR_CODE to SwInvalidConfig, clear the SHA-2 state, digest, packer and FIFO, and return to idle? Is any processing (e.g. a block compression in flight) allowed to continue with the old key after valid drops?

**Verified:** Other spec text lists what happens on revocation mid-operation: abort, SwInvalidConfig, SHA-2/digest/packer/FIFO cleared, return to idle, and no done interrupt. No spec text gives a cycle bound. None says whether an in-flight block compression may continue with the old key. The ambiguity stands.

**Searched:**

- revoke, sideload, key valid, SwInvalidConfig in hmac md docs in `hw/ip/hmac`: Behavioural description in theory_of_operation.md, programmers_guide.md and registers.md. No cycle bound and nothing on in-flight processing.
- revoke, key_valid, SwInvalidConfig in hmac/data in `hw/ip/hmac/data`: No matches.

**Looked at and ruled out:**

- [`hw/ip/hmac/doc/theory_of_operation.md:152-157`][26]: Lists the reaction steps but gives no timing and does not say whether a compression in flight may finish.

---

## 2. Spec contradicts itself

### 2.1 For SHA-2 384 the digest is read from DIGEST_0-DIGEST_12.

`f_f7e6f870b119` · description vs. definition · severity high

**Gap:** A 384-bit digest is twelve 32-bit words (DIGEST_0-DIGEST_11), but the text gives DIGEST_0-DIGEST_12, which is thirteen registers.

**Spec says:**

- [`hw/ip/hmac/README.md:42`][28]: `or from DIGEST_0-DIGEST_12 for SHA-2 384`

**Not answered by the spec:** Is the SHA-2 384 digest read from DIGEST_0-DIGEST_11 or DIGEST_0-DIGEST_12?

**Verified:** The README gives DIGEST_0-DIGEST_12 for SHA-2 384. A 384-bit digest is twelve 32-bit words, so the range should be DIGEST_0-DIGEST_11. The same sentence uses DIGEST_0-DIGEST_7 for 256 bits (8 words) and DIGEST_0-DIGEST_15 for 512 bits (16 words), so the 384 case is the odd one out. I did not read the RTL or the generated register code. I did check the hjson, which defines a DIGEST register array and says nothing more specific about SHA-2 384.

**Searched:**

- DIGEST_0-DIGEST_N ranges in HMAC docs in `hw/ip/hmac`: Only README.md:42 says DIGEST_12. The other docs use DIGEST_0-DIGEST_15 for the full register array.
- DIGEST register definition in `hw/ip/hmac/data/hmac.hjson`: A DIGEST register array is defined, with a description referring to DIGEST_0 to DIGEST_15. No 384-specific range is given.

**Same subject, also found as spec and test disagree** (`f_db661743ce37`, test expects the opposite): The test randomizes the digest size, includes SHA-2 384, and reads the digest registers. The scoreboard compares them against the expected digest.

**Test does:**

- [`hw/ip/hmac/dv/env/seq_lib/hmac_test_vectors_sha_vseq.sv:61`][29]: `digest_size inside {SHA2_256, SHA2_384, SHA2_512};` — stimulus; If no plusarg was given, randomize the digest size among the valid values.
- [`hw/ip/hmac/dv/env/seq_lib/hmac_test_vectors_sha_vseq.sv:137`][30]: `rd_digest();` — stimulus; Read the digest registers, which triggers the scoreboard comparison.
- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:496`][31]: `` `DV_CHECK_EQ(real_digest_val, exp_digest[digest_idx]) `` — checker check; Read digest word must equal exp_digest for the expected digest size (SHA-2 256 idx<8, 384 idx<12, 512 all) when not stopped and no wipe.

**Verified:** The README says SHA-2 384 is read from DIGEST_0-DIGEST_12, which is 13 words. The scoreboard checks only digest_idx < 12, which is DIGEST_0-DIGEST_11, i.e. 384 bits. The two disagree. The README is likely the side that is wrong, since 384 bits is 12 words and DIGEST_12 is not part of a 384-bit digest. I did not check the RTL or the register documentation.

**Further evidence found by the verifier:**

- [`hw/ip/hmac/README.md:41`][32]: ``or from [`DIGEST_0-DIGEST_12`](doc/registers.md#digest) for SHA-2 384`` — Spec gives a range of 13 words for SHA-2 384.

---

## 3. Test contradicts its own description

### 3.1 chip_sw_hmac_enc: plan vs. test

`f_79bd89c9d1fb` · plan vs. test · severity medium

**Gap:** The plan says the test verifies both the HMAC done and FIFO empty interrupts. The test only checks that the serviced IRQ is HMAC done, and only for the SHA256 key-hashing step (k3). It never checks a FIFO empty interrupt. The final HMAC operation over the reference data is completed by polling, so no interrupt is verified for it.

Labels in this finding: k3 = [`sw/device/tests/hmac_enc_test.c:98`][33]

**Test description says:**

- [`hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson:13-14`][34]: `Verify the HMAC done and FIFO empty interrupts as a part of this test.` — HMAC done and FIFO empty interrupts are verified.

**Test does:**

- [`sw/device/tests/hmac_enc_test.c:98`][33]: `CHECK(irq_serviced == expected_irq);` — check; The serviced IRQ is the HMAC done IRQ.
- [`sw/device/tests/hmac_enc_test.c:111`][35]: `hmac_testutils_finish_and_check_polled(&hmac, &kHmacRefExpectedDigest)` — stimulus; Poll for completion of the HMAC operation.

**Verified:** The plan item chip_sw_hmac_enc is explicitly linked to this test. It says the HMAC done and FIFO empty interrupts are verified. The test enables both IRQs but only checks irq_serviced == HMAC done, once, after the key-hash step. It never checks a FIFO empty IRQ, and the final HMAC is completed by polling with no interrupt check.

**Searched:**

- FIFO empty IRQ checks in the test source in `sw/device/tests/hmac_enc_test.c`: The FIFO empty IRQ is enabled at lines 63-70 but never checked. The handler stores irq_serviced and only the HMAC done value is compared.
- Plan-to-test link in `hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson`: The plan item lists bazel //sw/device/tests:hmac_enc_test, so the link is stated in the repository.

**Looked at and ruled out:**

- [`sw/device/tests/hmac_enc_test.c:61-70`][36]: This enables the FIFO empty interrupt in the HMAC IP and PLIC, but enabling it is not the same as verifying it.

---

### 3.2 hmac_multistream_functest: plan vs. test

`f_05c2b1da8b2e` · plan vs. test · severity low

**Gap:** Plan item p2 says each streamed operation is split into two chunks. The test instead picks a random segment count for each streaming vector with rand_testutils_gen32_range(1, 4). A vector can therefore be streamed in a single segment, or in more than two, so the fixed two-chunk save/restore scenario is not guaranteed.

Labels in this finding: p2 = [`hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson`][37]

**Test description says:**

- [`hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson`][37]: `HMAC context save/restore by streaming 6 SHA-2/HMAC operations in parallel in two chunks each.`

**Test does:**

- [`sw/device/tests/crypto/hmac_multistream_functest.c:109`][38]: `cur_ext_vec->segment_count = rand_testutils_gen32_range(1, 4);` — stimulus; Pick a random segment count for each streaming vector.

**Verified:** The plan item says each operation is streamed in two chunks. The test picks a random segment count per streaming vector, so a vector can be streamed in one segment or in more than two. Two chunks is not guaranteed. I did not read the helper that implements rand_testutils_gen32_range, so I did not confirm whether 4 is a possible value. Neither reading yields a fixed two-chunk split.

**Searched:**

- segment_count usage and plan link in `hmac_multistream_functest.c, chip_hmac_testplan.hjson`: Random segment count; the plan item links to this test via bazel target hmac_multistream_functest.

---

## 4. Spec and test disagree

No verified findings.

## 5. Spec not tested

### 5.1 Partly tested: 4 requirements in README.md > Description

`f_2f63ab250dc7` · severity high

1. **Requirement** `cr_03340331c331`: The module can wipe internal variables such as the secret key, intermediate hash results, digest and message scheduling array.  

   - [`hw/ip/hmac/README.md:59`][1]: `is able to wipe internal variables such as the secret key, intermediate hash results, digest and the internal message scheduling array.`

   **Tested by `hmac_wipe_secret`:** Randomly triggers wipe_secret during hashing sequences. The scoreboard checker verifies that the digest read after a wipe differs from the previously expected digest.  
   **Not tested:** Only the digest corruption is observed. Wiping of the secret key, intermediate hash state and message schedule array is not checked, nor is the digest content after the wipe.

   **Tested by `hmac_secure_wipe_test`:** Wipes with a known value after a keyed HMAC and checks every digest word equals the wipe value. A key-less restart then produces a digest different from the keyed one.  
   **Not tested:** Wiping of the secret key, intermediate hash state and message schedule array is not directly checked; only the digest registers and an indirect result are observed.

   **Verified:** Only digest corruption is checked after a wipe.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:482-484`][39]: `// If wipe_secret is triggered, ensure the predicted value does not match the read out` — The only wipe check is that the digest differs from the prediction; internal state is not checked.

2. **Requirement** `cr_9f6c9dac2641`: Only the key registers relevant to the configured key length are consumed (e.g. KEY_0-KEY_7 for a 256-bit key).  

   - [`hw/ip/hmac/README.md:40`][40]: `For example, to use a 256-bit secret key, CFG.key_length needs to be configured (as per register documentation) and then only the relevant secret key registers, only KEY_0-KEY_7 in this case, are consumed for the HMAC operation.`

   **Tested by `hmac_test_vectors_sha_vseq`:** Writes vector keys of varying valid lengths with the matching CFG key length, and the scoreboard compares the digest with NIST vectors. A digest that consumed the wrong key words would mismatch.  
   **Not tested:** Does not specifically show that key registers beyond the configured length are ignored, for example by filling them with different values.

   **Tested by `hmac_smoke`:** Randomizes key length and writes the key, and the scoreboard checks the digest against a reference model. This verifies the key length handling only end to end.  
   **Not tested:** No explicit check that key registers beyond the configured length are not consumed.

   **Verified:** No test fills key registers beyond the configured length with differing values.

3. **Requirement** `cr_aab3a12796d4`: Internal variables and the secret key are reset to the value written to WIPE_SECRET.  

   - [`hw/ip/hmac/README.md:62`][41]: `The internal variables and secret key will be reset to the written value.`

   **Tested by `hmac_secure_wipe_test`:** Wipes with UINT32_MAX and checks that every digest word equals the wipe value. It then recomputes without a key and checks that the digest differs from the expected one.  
   **Not tested:** Only the digest registers are observed, not the secret key, intermediate hash state or message schedule. Only one wipe value is used.

   **Verified:** No check that internal state equals the written wipe value.

4. **Requirement** `cr_b8debfc7325d`: A software/CPU write of a 32-bit value to WIPE_SECRET wipes the internal variables, including the secret key, intermediate hash results, digest and message scheduling array.  

   - [`hw/ip/hmac/README.md:61`][42]: `The software can wipe the internal variables and secret key by writing a 32-bit random value into WIPE_SECRET register.`
   - [`hw/ip/hmac/data/hmac.hjson:455`][43]: `If the CPU writes a value into the register, the value is used to clear some internal variables such as the secret key, intermediate hash results, digest and internal message scheduling array.`

   **Tested by `hmac_wipe_secret`:** Runs the smoke sequence with random wipe_secret writes. The scoreboard checks that the digest after the wipe differs from the expected one.  
   **Not tested:** Only digest corruption is observed. It does not check that the secret key, intermediate hash state and message schedule are cleared, nor that the wipe value is the one written.

   **Tested by `hmac_secure_wipe_test`:** Performs a secure wipe after a keyed HMAC and checks that the digest words are overwritten. It then checks that a keyless recomputation differs from the expected digest.  
   **Not tested:** Only the digest registers are checked directly. The wipe of the key, intermediate hash and message schedule is not directly verified.

   **Verified:** Same gap as above: only the digest is observed after a wipe.

Item 5 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- wipe_secret usage in the hmac dv directory in `hw/ip/hmac/dv`: Only the smoke-based wipe vseq and the scoreboard digest-differs check; no probing of key, hash state or message schedule.
- key-length coverage in `hw/ip/hmac/dv/env/hmac_env_cov.sv`: A key_length coverpoint and crosses exist; nothing forces all five lengths.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_env_cov.sv:71-87`][44]: Key-length coverage bins exist, but they are coverage only; they do not force every length and do not check that unused key words are ignored.

---

### 5.2 Partly tested: 5 requirements in hmac.hjson > CFG (and 3 more sections)

`f_7cbe691d8b88` · severity high

1. **Requirement** `cr_db590d70ed32`: With Key_None and unkeyed SHA-2 (hmac_en == 0), starting is not blocked.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > CFG

   - [`hw/ip/hmac/data/hmac.hjson:355`][45]: `If only unkeyed SHA-2 is configured (hmac_en == 0), starting is not blocked, since this does not require a key.`

   **Tested by `hmac_test_vectors_sha_vseq`:** Runs SHA-2-only vectors (non-HMAC mode) with a key length derived per vector. The digest is compared by the scoreboard, so a blocked start would be caught.  
   **Not tested:** It is not explicit that key_length is Key_None in the unkeyed runs, nor that hmac_en == 0 with Key_None is checked as 'not blocked'.

   **Verified:** No explicit hmac_en=0 with Key_None 'not blocked' check.

2. **Requirement** `cr_1089c5d4e1c7`: While the message FIFO is full, writes to it are blocked until the FIFO has space, causing back-pressure on the interconnect.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > STATUS

   - [`hw/ip/hmac/data/hmac.hjson:429`][46]: `Data written to the FIFO whilst it is full will cause back-pressure on the interconnect`
   - [`hw/ip/hmac/doc/programmers_guide.md:207`][47]: `Whilst the FIFO is full, the HMAC will block writes until the FIFO has space which will cause back-pressure on the interconnect.`

   **Tested by `hmac_back_pressure`:** The test runs the smoke flow with large messages, full-width writes and back pressure enabled. The scoreboard's digest comparison would catch a write lost while the FIFO was full.  
   **Not tested:** Nothing checks that the FIFO actually reached full or that writes are stalled until space is available. The back pressure enabled may be at the bus-agent level rather than FIFO-full blocking.

   **Verified:** Only FIFO-full status prediction. No check that writes are stalled.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:717-718`][48]: `end else if (hmac_fifo_full_posedge && hmac_fifo_full) begin` — The scoreboard tracks FIFO-full for status prediction. It does not check that writes are stalled.

3. **Requirement** `cr_c486e3895381`: hmac_idle indicates HMAC idle status.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > STATUS

   - [`hw/ip/hmac/data/hmac.hjson:417`][16]: `HMAC idle status.`

   **Tested by `hmac_test_vectors_hmac_vseq`:** The HMAC test-vector sequence runs full hash operations while the scoreboard checkers compare the idle pin against the expected value after reset, after start, and at done.  
   **Not tested:** No explicit read of STATUS.hmac_idle is shown, and the exact idle conditions are not exercised, for example the invalid-configuration path.

   **Tested by `hmac_directed`:** The directed smoke sequence runs hashes, with a reset applied at a pad FSM state, while the scoreboard idle checks compare the idle value against the expected one.  
   **Not tested:** STATUS.hmac_idle reads are not directly shown, and the idle conditions are not specifically targeted beyond the reset case.

   **Tested by `hmac_burst_wr`:** Long-message burst-write hashing runs with the scoreboard idle checks, which compare the idle value after start and at done.  
   **Not tested:** No direct STATUS.hmac_idle read is shown, and the idle-conditions definition is not specifically verified.

   **Verified:** No test of the idle conditions beyond the generic scoreboard prediction.

4. **Requirement** `cr_b0906404bd67`: The clear secret operation overwrites the internal variables with the provided 32-bit value.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > WIPE_SECRET

   - [`hw/ip/hmac/data/hmac.hjson:456`][49]: `The clear secret operation overwrites the internal variables with the provided 32-bit value.`

   **Tested by `hmac_wipe_secret`:** The test runs smoke-derived sequences with randomly triggered wipe_secret. The scoreboard checker reads the digest after a wipe and checks that it differs from the previously expected one.  
   **Not tested:** The test does not check that internal variables equal the written 32-bit value. It only checks that the digest differs. The key, intermediate state and 64-bit duplication are not verified.

   **Tested by `hmac_secure_wipe_test`:** The test writes WIPE_SECRET with UINT32_MAX and checks that every digest word equals the wipe value. It also checks that a later keyless HMAC result differs from the keyed one.  
   **Not tested:** Only the digest registers are checked, with one wipe value and 32-bit words. The key, intermediate hash state and message schedule array are not checked, and neither is the duplicated 64-bit value for SHA-2 384/512.

   **Verified:** Only a digest-differs check.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:482-484`][39]: `// If wipe_secret is triggered, ensure the predicted value does not match the read out` — After a wipe, the scoreboard only checks that the digest differs. It does not check the internal state.

5. **Requirement** `cr_00446a3b2658`: While the engine computes the hash, it discards attempts to update the secret key registers.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > KEY

   - [`hw/ip/hmac/data/hmac.hjson:482`][50]: `If the engine computes the hash, it discards any attempts to update the secret keys`

   **Tested by `hmac_error_conditions_test`:** The test attempts a key update while the HMAC is processing and checks that an error is raised.  
   **Not tested:** It does not check that the key registers were actually left unchanged (e.g. that the final digest still uses the original key).

   **Verified:** No check that the key is unchanged. The sequence only randomizes key writes.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:398`][51]: `// randomly change key, config regs during msg wr, should trigger error or be discarded` — Key writes during message writes are randomized. No check that the key is unchanged is evident.

**Searched:**

- hmac_en=0 with Key_None 'not blocked' check in `hw/ip/hmac/dv`: No explicit directed check. Key_None is only handled in invalid-configuration prediction.
- FIFO-full back-pressure check in `hw/ip/hmac/dv`: Only status prediction and a coverage cross. No check that writes are stalled.
- hmac_idle STATUS reads and idle conditions in `hw/ip/hmac/dv`: Idle is predicted from the pin. No targeted test of the idle conditions.
- wipe_secret internal state check in `hmac_scoreboard.sv`: Only a digest-differs check.
- key unchanged while hashing in `hw/ip/hmac/dv`: Only a comment about random key writes. No check that the key is unchanged.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_env_cov.sv:101`][52]: This is a coverage cross of FIFO-full, not a check.

---

### 5.3 Partly tested: 4 requirements in hmac.hjson > KEY (and 3 more sections)

`f_33a5c60968b6` · severity high

1. **Requirement** `cr_67bea9a867f4`: KEY_0-KEY_31 are ignored when the key is sideloaded from the key manager.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > KEY

   - [`hw/ip/hmac/data/hmac.hjson:484`][53]: `These registers are ignored when the key is sideloaded from the key manager,`
   - [`hw/ip/hmac/doc/programmers_guide.md:80`][54]: `When the sideloaded key is used, the KEY_0-KEY_31 registers and CFG.key_swap are ignored`
   - [`hw/ip/hmac/README.md:45`][55]: `in which case the KEY_0-KEY_31 registers are ignored`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test runs HMAC with a sideloaded key and shows the digest differs from the software-key digest and matches the DV expected digest. This shows the software key was not used.  
   **Not tested:** It does not show that KEY register writes are made and then ignored while the sideloaded key is in use.

   **Verified:** DV disables sideload. The C test does not write KEY while sideloaded.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56]: `// TODO(#31634): Disable sideloading for now.` — Sideload is never enabled in DV, so KEY writes while sideloaded are not tested there.

2. **Requirement** `cr_781c9e811c5e`: The KEY registers may be updated only when the engine is in Idle state.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > KEY

   - [`hw/ip/hmac/data/hmac.hjson:481`][57]: `The registers are allowed to be updated only when the engine is in Idle state.`

   **Tested by `hmac_error_conditions_test`:** It shows that a key write during an operation is rejected with an error.  
   **Not tested:** Only a key write while a hash is in process is checked; other non-idle states and successful updates when idle are not covered.

   **Verified:** Only a key write during a hash is covered.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:216-220`][58]: `task hmac_base_vseq::write_discard_key();` — KEY is written only during a hash as a discard write. Other non-idle states and successful updates when idle are not covered.

3. **Requirement** `cr_98ab14b5706d`: Software may write the DIGEST registers when STATUS.hmac_idle is 1.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > DIGEST

   - [`hw/ip/hmac/data/hmac.hjson:507`][59]: ``When `STATUS.hmac_idle` is 1, these registers may be written to by software.``

   **Tested by `sar_same_context`:** The sequence disables SHA and writes the saved digest back into the DIGEST registers, then resumes hashing. The scoreboard updates its expected digest on digest writes.  
   **Not tested:** The write happens after a done event with SHA disabled, not explicitly under STATUS.hmac_idle=1. The test shows no direct check that the write takes effect. It writes back the same value it read, so a dropped write might go unnoticed.

   **Verified:** The save-and-restore flow writes back the same digest, with no explicit hmac_idle precondition and no check that a different value takes effect.

4. **Requirement** `cr_ce024c03a454`: Software may write MSG_LENGTH_UPPER when STATUS.hmac_idle is 1.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > MSG_LENGTH_UPPER

   - [`hw/ip/hmac/data/hmac.hjson:545`][60]: ``When `STATUS.hmac_idle` is 1, this register may be written by software.``

   **Tested by `sar_different_context`:** Writes the message length registers while SHA is disabled and reads them back after resuming.  
   **Not tested:** Does not explicitly establish STATUS.hmac_idle=1 at the write, and only one branch checks the written value.

   **Verified:** hmac_idle is never read before the write and not every branch checks the written value.

Item 5 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload and key-write sequences in `hw/ip/hmac/dv/env/seq_lib`: Sideload is disabled in DV. Key writes during a hash are only discarded.
- hmac_idle used as a write precondition in `hw/ip/hmac/dv`: Used only in scoreboard status prediction. Not checked before KEY, DIGEST or MSG_LENGTH_UPPER writes.
- DIGEST and msg_length_upper writes with distinct values in `hw/ip/hmac/dv/env/seq_lib`: csr_wr_digest only restores the saved digest through the save-and-restore flow. No test writes a different value to check the write takes effect.
- sub-word and unaligned MSG_FIFO writes in `hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv`: Random wr_addr in the window with a random partial wr_mask. The digest is checked by the scoreboard.
- KEY and hmac_idle in the C tests in `sw/device/tests`: Only CFG key_length checks. No KEY-register-ignored check.

---

### 5.4 Partly tested: 4 requirements in programmers_guide.md > Using a sideloaded key (and 1 more section)

`f_e5e39744acb1` · severity high

1. **Requirement** `cr_0f006fc77258`: An aborted operation does not raise the hmac_done interrupt.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:113`][61]: `An aborted operation does not raise the hmac_done interrupt`
   - [`hw/ip/hmac/doc/theory_of_operation.md:157`][62]: ``No `hmac_done` interrupt is raised for an aborted operation.``

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test starts HMAC with an invalid sideload key and checks that hmac_err is raised with SwInvalidConfig and that the hmac_done interrupt is not set.  
   **Not tested:** It does not abort an operation that is already running by revoking the key mid-operation, so the no-done behavior of an aborted in-flight operation is not verified.

   **Verified:** No test aborts an in-flight operation by revoking the key and checks that hmac_done is not raised.

2. **Requirement** `cr_59caa8cb74a9`: Starting while the sideloaded key is still invalid is blocked with the same error.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:123`][22]: `Starting while it is still invalid is blocked with the same error.`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test starts HMAC with an invalid sideload key and checks that SwInvalidConfig is reported and the operation does not complete.  
   **Not tested:** The recovery scenario is not exercised: the start is not made after an earlier abort, with hmac_err cleared and the key still invalid.

   **Verified:** No test starts again after an abort, with hmac_err cleared and the key still invalid.

3. **Requirement** `cr_a578aba72de1`: These errors are signalled through INTR_STATE.hmac_err.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:112`][63]: `In all of these cases the error is signalled through INTR_STATE.hmac_err, as for any other HMAC error.`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test checks that hmac_err is set for the rejected hash_stop and for the start with an invalid sideload key.  
   **Not tested:** The abort when the key is revoked during a running operation is not exercised, so the hmac_err signalling is not checked for that case.

   **Verified:** No test checks hmac_err for an abort caused by revoking the key mid-operation.

4. **Requirement** `cr_fb14b2d5a9e6`: The CFG.key_length read-back value does not depend on the key manager providing a key and is available before an operation starts.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:82`][64]: `so the value read back does not depend on the key manager providing a key and is available before an operation is started`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test writes CFG with sideload and hmac_en set and checks that CFG.key_length reads back as KEY_512, the sideload interface width. This only happens in the run where the key manager has generated a valid key.  
   **Not tested:** No check that the read-back value is the same while the key manager provides no valid key, for example after the key is cleared. It is also not clear that CFG is read before an operation starts.

   **Verified:** No test checks the sideload key_length read-back while the key manager provides no valid key.

Item 5 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload and SwInvalidConfig in hmac dv in `hw/ip/hmac/dv`: Sideload appears only in env config and package files. No sideload abort or revoke sequence in the hmac dv sequences.
- key_length handling in smoke and scoreboard in `hw/ip/hmac/dv/env`: Random illegal key_length leads to invalid_cfg, and the scoreboard predicts SwInvalidConfig.

---

### 5.5 Partly tested: 3 requirements in theory_of_operation.md > Key sideloading (and 2 more sections)

`f_ebdd6cfd3e88` · severity high

1. **Requirement** `cr_d468d703ae52`: The sideloaded key length overrides the length configured in CFG.key_length.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:141`][65]: `so it overrides whatever is configured in CFG.key_length.`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** Runs HMAC with the sideloaded key and checks that CFG.key_length reads back as KEY_512, the sideload interface width, which shows the sideload length is in use.  
   **Not tested:** The test does not show that a different CFG.key_length was configured before the sideloaded run, so the override of a conflicting configured length is not clearly verified.

   **Verified:** The test never programs a conflicting CFG.key_length before the sideloaded run, so the override is not shown.
   - [`sw/device/tests/keymgr_dpe_sideload_hmac_test.c:52-55`][66]: `static const dif_hmac_transaction_t kSideloadConfig = {` — The sideload config has no conflicting key_length programmed before the run.

2. **Requirement** `cr_6bc711c2b717`: ERR_CODE reports SwInvalidConfig when hash_stop or hash_continue is rejected with a sideloaded key.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading > Restrictions with a sideloaded key

   - [`hw/ip/hmac/doc/theory_of_operation.md:164`][25]: `` [`ERR_CODE`](registers.md#err_code) reports `SwInvalidConfig` ``

   **Tested by `keymgr_dpe_sideload_hmac_test`:** During a sideloaded-key operation the test writes CMD.hash_stop. It then checks that the hmac_err interrupt is set and ERR_CODE equals SwInvalidConfig.  
   **Not tested:** Only hash_stop is exercised. hash_continue with a sideloaded key is never issued, so its SwInvalidConfig reporting is not verified.

   **Verified:** hash_continue is never issued with a sideloaded key; only hash_stop is.
   - [`sw/device/tests/keymgr_dpe_sideload_hmac_test.c:142-144`][67] — Only hash_stop is issued with a sideloaded key; hash_continue is never issued.
     ~~~
     mmio_region_write32(hmac.base_addr, HMAC_CMD_REG_OFFSET,
                         1u << HMAC_CMD_HASH_STOP_BIT);
     check_and_clear_invalid_config_error();
     ~~~

3. **Requirement** `cr_f3333982706d`: When a sideloaded key is used, CMD.hash_stop and CMD.hash_continue are rejected (saving and restoring the context is not available).  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading > Restrictions with a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:102`][24]: `Saving and restoring the context is not available`
   - [`hw/ip/hmac/doc/programmers_guide.md:102`][24]: `CMD.hash_stop and CMD.hash_continue are rejected`
   - [`hw/ip/hmac/doc/theory_of_operation.md:164`][25]: ``saving and restoring the context is not available with a sideloaded key: `CMD.hash_stop` and `CMD.hash_continue` are rejected``

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test attempts CMD.hash_stop during a sideloaded-key operation. It checks that the hmac_err interrupt is set and ERR_CODE is SwInvalidConfig, which shows the command is rejected.  
   **Not tested:** CMD.hash_continue with a sideloaded key is not tested. The test also does not check that the HMAC state is otherwise unchanged after the rejection.

   **Verified:** hash_continue is not tested with a sideloaded key, and there is no state-unchanged check after the rejected hash_stop beyond the error check.

Item 4 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload and hash_continue/SwInvalidConfig in the hmac DV env in `hw/ip/hmac/dv`: No sideload stimulus in the DV env. SwInvalidConfig is only used for invalid_cfg in the scoreboard.
- sideload in sw tests in `sw/device/tests`: Only keymgr_dpe_sideload_hmac_test; it checks hash_stop only.
- sub-word write stimulus in `hmac_base_vseq.sv wr_msg and burst_wr_msg`: wr_mask and wr_addr are randomized per write.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:198-219`][68]: SwInvalidConfig handling here is for the non-sideload invalid_cfg case, not for a sideloaded key.

---

### 5.6 Partly tested: 4 requirements in theory_of_operation.md > SHA-2 message feed and pad (and 2 more sections)

`f_d1a2b13aa461` · severity high

1. **Requirement** `cr_26f0103d3cea`: When the FIFO is full, writes are blocked, causing interconnect back-pressure rather than being dropped or overwriting FIFO contents.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > SHA-2 message feed and pad

   - [`hw/ip/hmac/doc/theory_of_operation.md:34`][69]: `If the FIFO is full, the HMAC block will block any writes leading to back-pressure on the interconnect (as opposed to dropping those writes or overwriting existing FIFO contents).`

   **Tested by `hmac_back_pressure`:** Heavy full-width writes with back pressure enabled, with the digest checked by the scoreboard. Dropped or overwritten writes would corrupt the digest.  
   **Not tested:** Does not guarantee the FIFO becomes full, and does not check that interconnect back-pressure is applied.

   **Verified:** No test checks that back-pressure is applied when the FIFO is full.

3. **Requirement** `cr_605b3802525f`: Revocation of the key by the key manager takes effect immediately.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:139`][27]: `the key manager revoking the key takes effect immediately`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** After the sideload key is cleared, the next HMAC start is blocked with SwInvalidConfig. This shows a revoked key is seen by HMAC, but only for an operation started afterwards.  
   **Not tested:** Revocation during an operation in progress, and any timing or immediacy of the effect, are not checked.

   **Verified:** No test revokes the key during an operation in progress or measures latency.

4. **Requirement** `cr_75181956206e`: ERR_CODE reports SwInvalidConfig when hash_start or hash_continue is triggered while the key is invalid.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:154`][21]: `ERR_CODE reports SwInvalidConfig.`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** The test clears the sideloaded key, starts HMAC with the invalid key, and checks that hmac_err is raised with ERR_CODE SwInvalidConfig and that hmac_done is not set.  
   **Not tested:** Only the CMD.hash_start path is exercised; triggering CMD.hash_continue while the key is invalid is not checked.

   **Verified:** hash_continue with an invalid sideload key is not exercised.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:217-219`][70]: `end else if (invalid_cfg) begin` — The scoreboard predicts SwInvalidConfig for hash_start or hash_continue, but only for configuration-driven invalid_cfg. It does not cover an invalid sideload key during hash_continue.

5. **Requirement** `cr_b5bc42e67d01`: Triggering CMD.hash_start or CMD.hash_continue while the sideloaded key is invalid is blocked.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:154`][21]: `Triggering CMD.hash_start or CMD.hash_continue while the key is invalid is blocked`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** Starts HMAC with an invalid sideload key (key cleared, no new randomness loaded) and checks that hmac_err is raised with SwInvalidConfig and hmac_done stays unset, so the start is blocked.  
   **Not tested:** Only CMD.hash_start is exercised with an invalid key; CMD.hash_continue with an invalid key is not tested.

   **Verified:** Only hash_start is blocked with an invalid sideload key in tests.

Item 2 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- hash_continue / SwInvalidConfig in the hmac dv directory in `hw/ip/hmac/dv`: Only configuration-driven invalid_cfg handling; no sideload-key-invalid continue test
- FIFO-full and back-pressure checks in `hw/ip/hmac/dv`: hmac_back_pressure_vseq only sets do_back_pressure. There is only a coverpoint on the FIFO-full status bit and no check that back-pressure is applied.
- sideload tests that use hmac in `hw/top_earlgrey/dv/env/seq_lib`: Only chip_sw_keymgr_dpe_sideload_hmac_vseq, which covers hash_start

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/seq_lib/hmac_back_pressure_vseq.sv:8-18`][71]: Does not guarantee the FIFO becomes full, and does not check that interconnect back-pressure is applied.

---

### 5.7 Test plan items with no test: 2 plan items

`f_d4135d06ef2a` · severity medium

2. **Plan item:** TB_LINT_COMPLETE — Testbench lint is complete, with CI lint reporting no errors or warnings and no waivers. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** This is a lint checklist item with no test. I did not check whether the CI lint results back the 'Done' status.

4. **Plan item:** tl_intg_err_cg — Coverage group for kinds of integrity errors (command, data, both) and number of error bits per integrity check, including byte-enabled writes on memories that store integrity values. (`hw/dv/tools/dvsim/testplans/tl_device_access_types_testplan.hjson`)

   **Verified:** It is a covergroup with no test mapped to it. I did not look for a separate TL integrity error test, so this is not a firm finding.
   - [`hw/dv/tools/dvsim/testplans/tl_device_access_types_testplan.hjson:21`][72]: `name: tl_intg_err_cg` — This is a covergroup, and no test is attached to it.

Items 1, 3, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- HMAC imports of common test and testplan hjsons in `hw/ip/hmac/dv/hmac_sim_cfg.hjson, hw/ip/hmac/data/hmac_testplan.hjson`: csr_tests and alert_test hjsons are imported, and the common testplans are imported.
- regwen testpoint test list in `csr_testplan.hjson`: It maps to csr_rw and csr_aliasing.

---

### 5.8 Test plan items with no test: 3 plan items

`f_f08983440cee` · severity medium

1. **Plan item:** SIM_CODE_COVERAGE_AT_100 — Simulation code coverage reaches 100%. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** A coverage-closure goal. No test implements it. The checklist cites an issue instead.
   - [`hw/ip/hmac/doc/checklist.md:247-249`][73]: `Coverage      | [SIM_CODE_COVERAGE_AT_100][]      | Done        | Documented in issue #25216` — The coverage checklist items are process sign-offs, not tests. FPV is N/A.

3. **Plan item:** SIM_FUNCTIONAL_COVERAGE_AT_100 — Simulation functional coverage reaches 100%. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** A coverage-closure goal. No test implements it.

5. **Plan item:** FPV_CODE_COVERAGE_AT_100 — FPV code coverage reaches 100%; marked N/A for HMAC. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** The item is marked N/A for HMAC, so it has no test.

Items 2, 4 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- fifo_empty_status_interrupt and csr_aliasing across hmac and hw/dv in `hw/ip/hmac, hw/dv`: The testplan lists tests for fifo_empty. The csr_aliasing test is generated from csr_tests.hjson.
- checklist coverage items in `hw/ip/hmac/doc/checklist.md`: SIM code and functional coverage at 100% are Done with issue references. FPV code coverage is N/A.

---

### 5.9 Test plan items with no test: 3 plan items

`f_f1c31aadbabb` · severity medium

1. **Plan item:** X_PROP_ANALYSIS_COMPLETED — X-propagation analysis is completed, evidenced by the xprop log reporting full instrumentation success. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** The only evidence cited is the xprop log; no test implements it.
   - [`hw/ip/hmac/doc/checklist.md:244`][74]: `xprop.log file says: "instrumentation success rate:  100%"` — X-prop item is evidenced by a tool log, not a test.

2. **Plan item:** ALL_TODOS_RESOLVED — All TODOs resolved; remaining TODOs have linked issues and are not needed for verification completion. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** A process item with no test.
   - [`hw/ip/hmac/doc/checklist.md:251`][75]: `Remaining TODOs have a linked issue and are not required for verification completion` — A process item with no test.

3. **Plan item:** PRE_VERIFIED_SUB_MODULES_V3 — Pre-verified sub-modules at V3; marked N/A. (`hw/ip/hmac/doc/checklist.md`)

   **Verified:** Marked N/A, so no test is expected.
   - [`hw/ip/hmac/doc/checklist.md:254`][76]: `[PRE_VERIFIED_SUB_MODULES_V3][]   | N/A` — Marked N/A.

**Searched:**

- X_PROP_ANALYSIS_COMPLETED / xprop in `hw/ip/hmac`: Only the checklist.md row, no test.
- ALL_TODOS_RESOLVED in `hw/ip/hmac`: Only the checklist.md row, no test.
- PRE_VERIFIED_SUB_MODULES_V3 in `hw/ip/hmac`: Only the checklist.md row, marked N/A, no test.

---

### 5.10 No test found: 4 requirements in README.md > Description

`f_453f3ccaac2a` · severity medium

1. **Requirement** `cr_1c762e6535de`: For SHA-2 384/512 modes operating on 64-bit words, the 32-bit wipe value is replicated and concatenated to form the 64-bit wipe value.  

   - [`hw/ip/hmac/README.md:63`][77]: `For SHA-2 384/512 modes that operate on 64-bit words, the 32-bit random value is replicated and concatenated to create the 64-bit value.`
   - [`hw/ip/hmac/data/hmac.hjson:457`][78]: `For SHA-2 384/512 that work with 64-bit words, the 32-bit value is duplicated and concatenated to generate the 64-bit value.`

   **Verified:** No check that the wipe value is the 32-bit value replicated to 64 bits. The scoreboard only checks that the digest differs after a wipe.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:482-483`][79]: `// If wipe_secret is triggered, ensure the predicted value does not match the read out` — After a wipe the scoreboard only checks that the digest differs from the prediction. It does not check the replicated 64-bit wipe value and does not check the message FIFO.

2. **Requirement** `cr_4282db4ce90a`: Software cannot read from the message FIFO.  

   - [`hw/ip/hmac/README.md:60`][3]: `which SW writes the message to (but cannot read from).`

   **Verified:** I found no test or assertion that checks SW cannot read the message FIFO.

3. **Requirement** `cr_4977ddf1d188`: For SHA-2 384 the digest is read from DIGEST_0-DIGEST_12.  

   - [`hw/ip/hmac/README.md:42`][28]: `or from DIGEST_0-DIGEST_12 for SHA-2 384`

   **Verified:** The scoreboard checks SHA-384 digests only for DIGEST_0–11. DIGEST_12 is not covered.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:491`][80]: `// only check till digest_idx = 7 for SHA-2 256 and till digest_idx = 11 for SHA-2 384` — For SHA-2 384 the scoreboard checks DIGEST_0–11 only. The spec's DIGEST_0-DIGEST_12 is not checked, and the range differs from the spec text.

4. **Requirement** `cr_8e41a2412a72`: Wiping does not clear the message FIFO.  

   - [`hw/ip/hmac/README.md:60`][3]: `It does not wipe the message FIFO`

   **Verified:** No test checks that the message FIFO is left intact after a wipe.

**Searched:**

- wipe tests and checks in the HMAC DV environment in `hw/ip/hmac/dv`: hmac_wipe_secret_vseq and the scoreboard's DV_CHECK_NE of the digest after a wipe. Nothing on the replicated wipe value or the message FIFO.
- SW tests that use wipe_secret in `sw/device/tests/hmac_secure_wipe_test.c`: It only processes a message and polls for the FIFO to be empty. It has no check on replication, FIFO read-back or FIFO contents after a wipe.

**Looked at and ruled out:**

- [`sw/device/tests/hmac_secure_wipe_test.c:51-55`][81]: It checks FIFO emptiness after processing a message. It does not check that a wipe leaves the FIFO untouched.

---

### 5.11 No test found: 1 requirement in hmac.hjson > CFG

`f_43010cec775f` · severity medium

4. **Requirement** `cr_5a80d0a89e9e`: CFG.sideload: hardware receives a write-qualifier pulse on software writes (true).  

   - [`hw/ip/hmac/data/hmac.hjson:203`][82]: `hwqe:     "true"`

   **Verified:** Sideload is forced to 0 in DV, so software writes of CFG.sideload never reach the hwqe path.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56]: `// TODO(#31634): Disable sideloading for now.` — Sideload is forced to 0 in random config, so hwqe on sideload is never exercised.

Items 1, 2, 3, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload in hmac DV seq_lib/scoreboard in `hw/ip/hmac/dv/env`: Only the agent setup, plus the line in base_vseq that forces sideload to 0.
- sha_en, digest_swap and digest_size in DV in `hw/ip/hmac/dv/env`: Used in sequences and modelled in the scoreboard, which checks the digest.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:353`][83]: Checks digest_swap behaviour, not the hwext attribute itself.

---

### 5.12 Partly tested: 1 requirement in hmac.hjson > CFG

`f_81c7d089aa21` · severity medium

2. **Requirement** `cr_403238c02dda`: key_length value is irrelevant when the key is sideloaded.  

   - [`hw/ip/hmac/data/hmac.hjson:315`][84]: `The value of this register is irrelevant when HMAC is used as a plain SHA-2 hash function without a key (i.e., CFG.hmac_en is 0), and when the key is sideloaded from the key manager (see CFG.sideload).`

   **Tested by `keymgr_dpe_sideload_hmac_test`:** Runs HMAC with a keymgr_dpe sideloaded key and checks the digest, and the CFG key_length field reads back as KEY_512 during the sideloaded run. This shows hardware overriding the key length for sideload.  
   **Not tested:** Does not program different key_length values under sideload to show the configured value has no effect on the digest.

   **Verified:** Only the keymgr_dpe sideload test exists, and I found no stimulus that varies key_length under sideload.

Items 1, 3, 4, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- key_length, SHA2_None and sideload in hw/ip/hmac/dv in `hw/ip/hmac/dv`: Randomized key_length and digest_size including illegal values, scoreboard prediction of the casts, and invalid-config checks. Sideload appears only in the tb and env plumbing. I found no sequence that varies key_length under sideload.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85]: This covers the hash_start and process invalid-config path. It does not clearly show a distinct hash_continue trigger being blocked.

---

### 5.13 No test found: 5 requirements in hmac.hjson > CFG

`f_d9c388fe5832` · severity medium

1. **Requirement** `cr_6d3cb7350c6c`: CFG.hmac_en: hardware receives a write-qualifier pulse on software writes (true).  

   - [`hw/ip/hmac/data/hmac.hjson:203`][82]: `hwqe:     "true"`

   **Verified:** No check on the hmac_en qe pulse.
   - [`hw/ip/hmac/rtl/hmac_reg_top.sv:643`][86]: `assign cfg_qe = &cfg_flds_we;` — The RTL generates the CFG qe pulse. I found no DV check on it.

2. **Requirement** `cr_98f6488c2d66`: CFG.hmac_en: the register is implemented outside the register block (true).  

   - [`hw/ip/hmac/data/hmac.hjson:202`][87]: `hwext:    "true"`

   **Verified:** No direct check that hmac_en is implemented outside the register block.

3. **Requirement** `cr_afe1daf5c85e`: CFG.digest_size: hardware receives a write-qualifier pulse on software writes (true).  

   - [`hw/ip/hmac/data/hmac.hjson:203`][82]: `hwqe:     "true"`

   **Verified:** No check on the digest_size qe pulse.

4. **Requirement** `cr_bf372f75531c`: CFG.key_length: the register is implemented outside the register block (true).  

   - [`hw/ip/hmac/data/hmac.hjson:202`][87]: `hwext:    "true"`

   **Verified:** No direct check that key_length is implemented outside the register block.

5. **Requirement** `cr_bf833396a76a`: CFG.endian_swap: the register is implemented outside the register block (true).  

   - [`hw/ip/hmac/data/hmac.hjson:202`][87]: `hwext:    "true"`

   **Verified:** No direct check that endian_swap is implemented outside the register block.

**Searched:**

- hmac_en: hwext/hwqe/qe checks in HMAC RTL and DV in `hw/ip/hmac`: Only generated reg_top RTL. No DV assertion on hmac_en qe or hwext.
- digest_size: qe pulse checks and uses in DV in `hw/ip/hmac/dv`: Only functional set/get/read in the sequences. No qe check.
- key_length: hwext checks in `hw/ip/hmac/dv`: Only functional csr_rd and csr_update. No structural check.
- endian_swap: hwext checks in `hw/ip/hmac/dv`: Only functional set/get. No structural check.
- cfg_qe or cfg.qe references in `hw/ip/hmac/dv`: None.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:162-169`][88]: These functional CFG writes are checked indirectly through the digest results. They do not verify hwext or hwqe, so I count this only as a near miss. I did not inspect the scoreboard or the generic CSR tests in detail.

---

### 5.14 Only weak evidence (no check that can fail): 2 requirements in hmac.hjson > CFG (and 2 more sections)

`f_8fcf9e9698db` · severity medium

2. **Requirement** `cr_302cc8a11faa`: Writing 1 to hash_stop causes the hmac_done interrupt to be set once the current block has been hashed.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > CMD

   - [`hw/ip/hmac/data/hmac.hjson:396`][89]: ``When 1 is written to this field, SHA-2 or HMAC will afterwards set the `hmac_done` interrupt as soon as the current block has been hashed.``

   **Nearest evidence (not counted as a test):** `sar_same_context` (stimulus only, no check); `sar_stop_and_continue` (stimulus only, no check); `sar_different_context` (stimulus only, no check)

   **Verified:** There is no explicit check that hmac_done is set after hash_stop. The scoreboard only predicts it from the observed value, and the sar_* sequences only wait for it.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:413-417`][90]: `if (sha_en && (hmac_process || hmac_stopped)) begin` — hmac_done is only predicted after it is observed set. A missing done interrupt is not flagged by the scoreboard; the only catch is the sequences' wait for it timing out.

3. **Requirement** `cr_ef5931e3a47f`: After hash_stop completes, the hash can be read from DIGEST_0 to DIGEST_15.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > CMD

   - [`hw/ip/hmac/data/hmac.hjson:397`][91]: `The hash can then be read from the registers !!DIGEST_0 to !!DIGEST_15.`

   **Nearest evidence (not counted as a test):** `sar_same_context` (stimulus only, no check)

   **Verified:** The scoreboard skips digest comparison when hmac_stopped is set. The intermediate digest after hash_stop is never checked.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:476-484`][92]: `// compare digest. Only the final digest will be compared in case of S&R, this is enough` — Digest comparison is skipped (`if (!hmac_stopped)`) after hash_stop, so the digest after hash_stop is never compared. The code carries a TODO for this.

Items 1, 4 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- key_length, 1024 and hash_stop in the hmac dv directory in `hw/ip/hmac/dv`: The scoreboard feeds key_length to the DPI HMAC model and compares digests. The sequences randomise key_length with 1024 included (key_length_c). cfg_cg covers it.
- digest comparison after hash_stop in `hmac_scoreboard.sv`: Comparison is skipped when hmac_stopped is set, and a TODO (#23240) notes the missing intermediate-digest model.

---

### 5.15 No test found: 1 requirement in hmac.hjson > DIGEST (and 2 more sections)

`f_dcd02c1afd9c` · severity medium

4. **Requirement** `cr_959451b086ac`: The lower 3 bits [2:0] of the message length are ignored.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > MSG_LENGTH_LOWER

   - [`hw/ip/hmac/data/hmac.hjson:529`][14]: `Lower 3 bits [2:0] are ignored.`

   **Verified:** No stimulus writes nonzero values to bits [2:0]. The SAR values are multiples of 1024, and the comment says other values are unsupported.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:700-707`][93]: `// Override the msg_length value to be able to verify the upper part of the register.` — SAR test writes only multiples of 512/1024, so bits [2:0] are never nonzero.

Items 1, 2, 3, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- msg_length usage in the hmac dv directory in `hw/ip/hmac/dv`: SAR writes and reads in hmac_base_vseq. The only explicit check is DV_CHECK_EQ at line 726, and its inputs are multiples of 512/1024.
- scoreboard handling of digest and msg_length writes in `hmac_scoreboard.sv`: Predicted on write when sha_en is off. Digest reads are checked against exp_digest.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:522-541`][94]: Message length reads are only sampled for coverage, and the read check is disabled when sar_skip_ctxt is set. No low-bits-ignored check.

---

### 5.16 No test found: 2 requirements in hmac.hjson > interrupt_list (and 1 more section)

`f_4b2e04b5ed0c` · severity medium

1. **Requirement** `cr_8532dccc2de0`: The fifo_empty interrupt is not raised while the HMAC block is running in HMAC mode and performing the second round, i.e. computing the final hash of the outer key and the first-round result obtained with the inner key.  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > interrupt_list

   - [`hw/ip/hmac/data/hmac.hjson:70`][95]: `i) The HMAC block is not running in HMAC mode and performing the second round of computing the final hash of the outer key as well as the result of the first round using the inner key.`
   - [`hw/ip/hmac/doc/interfaces.md:24`][96]: `i) The HMAC block is not running in HMAC mode and performing the second round of computing the final hash of the outer key as well as the result of the first round using the inner key.`

   **Verified:** The scoreboard predicts fifo_empty without modelling the HMAC second-round condition, so suppression during the second round is not checked.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:693`][97]: `if (fifo_full_detected && hmac_fifo_empty) begin` — The fifo_empty prediction does not model the HMAC second-round suppression.

2. **Requirement** `cr_01eaae6544c4`: CFG.sideload: the register is implemented outside the register block (true).  
   Section: hw/ip/hmac/data/hmac.hjson > hmac > CFG

   - [`hw/ip/hmac/data/hmac.hjson:202`][87]: `hwext:    "true"`

   **Verified:** Sideload is disabled in the DV sequences, so hwext behaviour of CFG.sideload is not tested.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211`][98]: `// TODO(#31634): Disable sideloading for now.` — Sideload is forced to 0 in the DV sequences.

Items 3, 4, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- fifo_empty in hmac dv in `hw/ip/hmac/dv`: The scoreboard predicts the interrupt from fifo_full_detected and an empty FIFO only. The HMAC second-round condition appears only in a comment.
- sideload, digest_swap, key_length in hmac dv in `hw/ip/hmac/dv`: Sideload is disabled by a TODO. digest_swap and key_length are randomized in smoke and the test-vector vseqs, and digest results are compared.
- digest cleared on sha_en disable in `hw/ip/hmac/dv/env`: The smoke vseq disables sha_en and the scoreboard expects a zero digest.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_env_cov.sv:100`][99]: The fifo_empty cross is coverage only, with no check of second-round suppression.

---

### 5.17 No test found: 5 requirements in interfaces.md > Hardware Interfaces (and 1 more section)

`f_78aab0dc03f7` · severity medium

1. **Requirement** `cr_179a3654a746`: The hmac module has no TL-UL bus host interfaces.  
   Section: hw/ip/hmac/doc/interfaces.md > Hardware Interfaces

   - [`hw/ip/hmac/doc/interfaces.md:8`][100]: `Bus Host Interfaces (TL-UL): *none*`

   **Verified:** Static statement; no test checks it.

2. **Requirement** `cr_1ce0d9febabc`: The hmac module uses clk_i as its primary clock.  
   Section: hw/ip/hmac/doc/interfaces.md > Hardware Interfaces

   - [`hw/ip/hmac/doc/interfaces.md:5`][101]: ``Primary Clock: **`clk_i`**``

   **Verified:** No dedicated check that clk_i is the primary clock.

3. **Requirement** `cr_3627dd0601ce`: The hmac module has no peripheral pins for chip IO.  
   Section: hw/ip/hmac/doc/interfaces.md > Hardware Interfaces

   - [`hw/ip/hmac/doc/interfaces.md:9`][102]: `Peripheral Pins for Chip IO: *none*`

   **Verified:** There are no pins, so nothing to test.

4. **Requirement** `cr_b923d18300df`: The hmac module has no other clocks.  
   Section: hw/ip/hmac/doc/interfaces.md > Hardware Interfaces

   - [`hw/ip/hmac/doc/interfaces.md:6`][103]: `Other Clocks: *none*`

   **Verified:** Static statement; no test checks it.

5. **Requirement** `cr_2677d0527dcb`: hmac_done is an Event-type interrupt.  
   Section: hw/ip/hmac/doc/interfaces.md > Hardware Interfaces > Interrupts

   - [`hw/ip/hmac/doc/interfaces.md:23`][104]: `hmac_done`
   - [`hw/ip/hmac/doc/interfaces.md:23`][104]: `Event`

   **Verified:** The scoreboard checks hmac_done firing but not its Event type. This is weak and could arguably be withdrawn.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:416`][105]: `void'(ral.intr_state.hmac_done.predict(.value(1), .kind(UVM_PREDICT_READ)));` — Scoreboard predicts hmac_done interrupt state; it checks the interrupt firing, not its Event type.

**Searched:**

- hmac_done usage in DV in `hw/ip/hmac/dv`: smoke vseq, scoreboard and tb use it; no check for Event-type semantics
- TL-UL host interface checks (item 1) in `hw/ip/hmac/dv, hw/ip/hmac/doc/interfaces.md`: only the doc statement; no test checks it
- clk_i primary clock check (item 2) in `hw/ip/hmac/dv`: the env drives clk_i; no check that it is the primary clock
- chip IO pins (item 3) in `hw/ip/hmac/dv`: no pins exist, so nothing to test
- other clocks (item 4) in `hw/ip/hmac/dv`: no other clocks and no check

---

### 5.18 Only weak evidence (no check that can fail): 1 requirement in programmers_guide.md > Saving and restoring the context

`f_39ed7bdc4973` · severity medium

1. **Requirement** `cr_9b457cf2a7cd`: Context switches are possible only at the boundary of complete message blocks.  

   - [`hw/ip/hmac/doc/programmers_guide.md:144`][106]: `Such context switches are possible only at the boundary of complete message blocks`

   **Nearest evidence (not counted as a test):** `save_and_restore` (stimulus only, no check)

   **Verified:** Only a positive stimulus at the boundary exists. No test tries a mid-block context switch, and no check would fail if one were allowed.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:554-556`][107]: `// Wait until message transmission is on a block boundary (multiple of 512 bits in SHA-2 256` — Stimulus only ever saves at a block boundary; no negative case.

**Searched:**

- save_and_restore, block boundary, mid-block, partial, unaligned in `hw/ip/hmac`: Only the boundary-wait in the vseq, a coverage group and the scoreboard's rounding of the message counter. No mid-block save test and no check for one.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:239`][108]: Rounds the message counter up to the block boundary for prediction. This is not a check on the context-switch restriction.

---

### 5.19 No test found: 1 requirement in programmers_guide.md > Saving and restoring the context (and 2 more sections)

`f_ca4f733ef3eb` · severity medium

3. **Requirement** `cr_4eab539dc535`: SwPushMsgWhenShaDisabled has ERR_CODE value 0x1.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Errors

   - [`hw/ip/hmac/doc/programmers_guide.md:179`][109]: `SwPushMsgWhenShaDisabled`
   - [`hw/ip/hmac/doc/programmers_guide.md:179`][109]: `0x1`

   **Verified:** Code is unused and its coverage bin is commented out.
   - [`hw/ip/hmac/dv/env/hmac_env_cov.sv:109`][110]: `// bins push_msg_when_sha_disabled   = {SwPushMsgWhenShaDisabled};` — Code 0x1 coverage is commented out, so it is not tested.

Items 1, 2, 4, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- error code names in `hw/ip/hmac`: Scoreboard handles SwPushMsgWhenDisallowed and SwHashStartWhenShaDisabled; SwPushMsgWhenShaDisabled has no check.
- fifo_empty in dv in `hw/ip/hmac/dv`: Only a scoreboard prediction of status and interrupt, with a smoke vseq comment that cycle-level alignment is skipped.

---

### 5.20 No test found: 5 requirements in programmers_guide.md > Triggering HMAC/SHA-2 engine (and 1 more section)

`f_e9ce8dbfdcba` · severity medium

1. **Requirement** `cr_09387e293565`: For a message larger than 512 bits, software waits until the FIFO is not full before writing further bits.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Triggering HMAC/SHA-2 engine

   - [`hw/ip/hmac/doc/programmers_guide.md:42`][111]: `If the message is larger than 512-bit, the software must wait until the FIFO is not full before writing further bits.`

   **Verified:** This is a software rule. The DV checks the FIFO-full status bit but nothing enforces or checks the wait-before-write behavior.

2. **Requirement** `cr_6fdc529bb101`: CFG.sha_en is set only after all other configuration inputs have been set.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Triggering HMAC/SHA-2 engine

   - [`hw/ip/hmac/doc/programmers_guide.md:41`][112]: `once all other configuration inputs have been properly set`

   **Verified:** hmac_init writes all of CFG in a single write, and nothing checks the ordering of sha_en against the other configuration.

3. **Requirement** `cr_03c30ada2ff4`: If the key manager revokes the key during an operation, the operation is aborted.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:111`][113]: `If the key manager revokes the key while an operation is running, that operation is aborted`

   **Verified:** Sideload is disabled in the DV and no test revokes the key during an operation. Only RTL assertions exist.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56] — Random configs force sideload off, so no sideload test runs. This covers items 3, 4 and 5.
     ~~~
     // TODO(#31634): Disable sideloading for now.
       rand_config_value[ral.cfg.sideload.get_lsb_pos()] = 1'b0;
     ~~~
   - [`hw/ip/hmac/rtl/hmac.sv:1061-1062`][114] — RTL assertions exist, but nothing in the DV makes the key invalid mid-operation. They do not check that the message and intermediate hash are discarded, or that a key change is handled.
     ~~~
     `ASSERT(SideloadKeyInvalidBlocksStart_A, sideload_key_invalid |-> !hash_start_or_continue)
       `ASSERT(SideloadKeyInvalidAborts_A, sideload_key_invalid |-> !sha_en_engine)
     ~~~

4. **Requirement** `cr_2afcaa91c549`: After an abort, the part of the message already written is discarded together with the intermediate hash.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:125`][115]: `The part of the message that had been written is discarded together with the intermediate hash, and the context cannot be restored with a sideloaded key.`

   **Verified:** No abort and restart scenario is tested.

5. **Requirement** `cr_371670d7ec74`: The sideloaded key must remain unchanged for the entire operation.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:107`][116]: `The key also has to remain unchanged for the entire operation.`

   **Verified:** No test changes the sideloaded key during an operation.

**Searched:**

- item cr_09387e293565: FIFO-full waiting (fifo_full, wait_fifo) in the DV in `hw/ip/hmac/dv/env`: The scoreboard tracks the FIFO-full status bit and the coverage samples it. Nothing checks that software waits before writing.
- item cr_6fdc529bb101: sha_en ordering (sha_en) in the DV sequences in `hw/ip/hmac/dv/env/seq_lib`: hmac_init writes CFG in one go. Other sequences toggle sha_en for stop/continue and invalid-config cases, not for configuration ordering.
- item cr_03c30ada2ff4: key_valid, revoke and key_invalid in the DV in `hw/ip/hmac`: Only RTL matches (hmac.sv abort logic and assertions). No DV stimulus drives the key invalid, and the sideload agent has start_default_seq=0.
- item cr_2afcaa91c549: abort and restart, message discarded in `hw/ip/hmac/dv`: Nothing found. The scoreboard's SwInvalidConfig handling relates only to invalid CFG.
- item cr_371670d7ec74: key unchanged during an operation, sideload sequences in `hw/ip/hmac/dv/env/seq_lib`: Sideload is forced to 0 in hmac_base_vseq.sv. No test changes the key mid-operation.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_env_cov.sv:96-101`][117]: Covers the FIFO-full status bit only. It does not check that software waits for the FIFO to drain before writing.

---

### 5.21 No test found: 5 requirements in programmers_guide.md > Using a sideloaded key

`f_88522f88db26` · severity medium

1. **Requirement** `cr_419b9981b884`: A sideloaded key is never exposed to software.  

   - [`hw/ip/hmac/doc/programmers_guide.md:77`][118]: `The key is then never exposed to software.`
   - [`hw/ip/hmac/README.md:45`][55]: `the key is never exposed to software.`

   **Verified:** No test checks that software cannot read the sideloaded key.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56] — Random CFG never enables sideload, so no sideload stimulus is generated in block DV.
     ~~~
     // TODO(#31634): Disable sideloading for now.
     rand_config_value[ral.cfg.sideload.get_lsb_pos()] = 1'b0;
     ~~~

2. **Requirement** `cr_4311569af12c`: If the key is revoked during an operation, the digest is cleared.  

   - [`hw/ip/hmac/doc/programmers_guide.md:111`][113]: `the digest is cleared`

   **Verified:** No stimulus revokes the key mid-operation, and no check of digest clearing.

3. **Requirement** `cr_90db0cc9a415`: The CFG configuration is retained across an abort.  

   - [`hw/ip/hmac/doc/programmers_guide.md:127`][119]: `The configuration in CFG is retained across an abort and does not need to be written again.`

   **Verified:** No abort-with-sideload test that checks CFG retention.

4. **Requirement** `cr_b276172d406a`: If the sideloaded key becomes invalid (key revoked / valid deasserted) while an operation is in progress, the operation is aborted and ERR_CODE reports SwInvalidConfig.  

   - [`hw/ip/hmac/doc/programmers_guide.md:111`][113]: `and the same error is reported`
   - [`hw/ip/hmac/doc/programmers_guide.md:186`][120]: `It is also reported for the sideloaded key`
   - [`hw/ip/hmac/doc/programmers_guide.md:186`][120]: `when the key becomes invalid while an operation is in progress`
   - [`hw/ip/hmac/doc/theory_of_operation.md:155`][121]: `and reports the same error.`

   **Verified:** Sideload is disabled in the vseq, so the scoreboard's SwInvalidConfig prediction for key invalidation is never exercised.

5. **Requirement** `cr_bd802d2e0596`: The valid indication of the sideload interface must remain set for the entire operation.  

   - [`hw/ip/hmac/doc/programmers_guide.md:106`][122]: `This indication has to remain set for the entire operation.`

   **Verified:** No test drives or checks the valid indication across an operation.

**Searched:**

- key never exposed to software: sideload/key reads in hmac dv in `hw/ip/hmac/dv`: No check; sideload stimulus is disabled in the base vseq.
- key revoke / digest cleared: revok, key_valid in hmac dv in `hw/ip/hmac/dv`: No revocation sequence or digest-clear check.
- CFG retained across abort with sideload in `hw/ip/hmac/dv and hw/top_earlgrey/dv`: No test; sideload bit forced to 0.
- SwInvalidConfig on key becoming invalid in `hw/ip/hmac/dv`: The scoreboard predicts SwInvalidConfig at lines 199 and 219, but without sideload stimulus this is never exercised.
- valid held for the entire operation in `hw/ip/hmac/dv, hw/top_earlgrey/dv`: No check; the only sideload test is the chip-level keymgr_dpe_sideload_hmac vseq, which I did not read.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:199-219`][123]: Scoreboard predicts SwInvalidConfig, but no stimulus enables sideload or revokes the key.
- [`hw/top_earlgrey/dv/env/seq_lib/chip_sw_keymgr_dpe_sideload_hmac_vseq.sv:1`][124]: Chip-level basic sideload flow; not read in full, and likely does not revoke the key mid-operation or check abort, digest clearing or CFG retention.

---

### 5.22 No test found: 2 requirements in programmers_guide.md > Using a sideloaded key (and 1 more section)

`f_dbc685521d55` · severity medium

1. **Requirement** `cr_c8ea510e0e76`: A key becoming invalid after operation completion clears the digest.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:116`][125]: `A key that becomes invalid after the operation has completed clears the digest`
   - [`hw/ip/hmac/doc/programmers_guide.md:116`][125]: `so the digest registers then silently read as zero`

   **Verified:** Sideload is disabled in DV and no test invalidates the key after completion.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56]: `// TODO(#31634): Disable sideloading for now.` — Sideload is forced off in randomized configs, so a key becoming invalid is never exercised.

2. **Requirement** `cr_cf99e411151d`: No error is reported when the key becomes invalid after the operation has completed.  
   Section: hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Using a sideloaded key

   - [`hw/ip/hmac/doc/programmers_guide.md:116`][125]: `and no error is reported in this case`

   **Verified:** Same as the previous item: no invalid-key-after-completion stimulus exists, so there is no check of the absence of an error.

Items 3, 4, 5 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload and key-invalid handling in the DV in `hw/ip/hmac/dv`: Sideload is disabled in randomization (TODO #31634). No test or check for the key becoming invalid after completion.
- digest clear on sha_en falling, and digest write lock in `hmac_scoreboard.sv`: The clear is modelled and checked on digest reads. The write lock is modelled in the scoreboard, but I found no confirming stimulus.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:1041-1056`][126]: This models the pending-error rule for item 3, but I found no stimulus that exercises it, so it is inconclusive.

---

### 5.23 No test found: 5 requirements in theory_of_operation.md > Block Diagram (and 2 more sections)

`f_1150de25abf8` · severity medium

1. **Requirement** `cr_b09d206cb6b3`: The SHA-2 message scheduling FIFO is not software accessible.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Block Diagram

   - [`hw/ip/hmac/doc/theory_of_operation.md:17`][127]: `The message scheduling FIFO is not software accessible`

   **Verified:** No test attempts or checks software access to the scheduling FIFO.

2. **Requirement** `cr_fd137b45d0d9`: The SHA-2 hash engine is a SHA-2 engine primitive instantiated with multi-mode feature enabled.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Block Diagram

   - [`hw/ip/hmac/doc/theory_of_operation.md:7`][128]: `which is a SHA-2 engine primitive instantiated with the multi-mode feature enabled`

   **Verified:** Multi-mode instantiation appears only in coverage exclusions, with no check.
   - [`hw/ip/hmac/dv/cov/hmac_unr_excl.el:13`][129]: `INSTANCE: tb.dut.u_prim_sha2_512.gen_multimode_logic.u_prim_sha2_multimode.u_pad` — Multi-mode primitive appears only in coverage exclusions, not in checks.

3. **Requirement** `cr_6241cc5595f4`: Packed words are fed into the internal message scheduling FIFO.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > SHA-2 message feed and pad

   - [`hw/ip/hmac/doc/theory_of_operation.md:57`][130]: `These words are fed into the internal message scheduling FIFO.`

   **Verified:** No check on the packer-to-scheduling-FIFO path beyond end-to-end digest comparison.
   - [`hw/ip/hmac/doc/theory_of_operation.md:56`][131]: ``Small writes to [`MSG_FIFO`](registers.md#msg_fifo) are coalesced into 32-bit words`` — Packer coalescing is only exercised indirectly by end-to-end digest comparison.

4. **Requirement** `cr_e937e36d780f`: Small writes to MSG_FIFO are coalesced into 32-bit words by the packer logic.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > SHA-2 message feed and pad

   - [`hw/ip/hmac/doc/theory_of_operation.md:56`][131]: `Small writes to MSG_FIFO are coalesced into 32-bit words by the packer logic.`

   **Verified:** Coalescing into 32-bit words is not checked directly. Only digests are compared.

5. **Requirement** `cr_236bbacf0c98`: In each round the compression function fetches a 64-bit word from the buffer and computes the internal variables.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > SHA-2 computation

   - [`hw/ip/hmac/doc/theory_of_operation.md:83`][132]: `In each round, the compression function fetches a 64-bit word from the buffer and computes the internal variables.`

   **Verified:** No check on the per-round 64-bit word fetch in the compression function.

**Searched:**

- software access to the scheduling FIFO in `hw/ip/hmac/dv`: No test or check for it.
- multimode instantiation checks in `hw/ip/hmac/dv`: Only coverage exclusion entries, no assertion or check.
- packer to scheduling FIFO feeding in `hw/ip/hmac/dv`: Only the scoreboard's MSG_FIFO modelling. No check on the internal path.
- packer coalescing into 32-bit words in `hw/ip/hmac/dv`: Only end-to-end digest comparison.
- per-round 64-bit word fetch in the compression function in `hw/ip/hmac/dv`: No reference.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/seq_lib/hmac_smoke_vseq.sv:211`][133]: A comment about the sha2_pad state transition. It does not check packer coalescing or the scheduling FIFO.

---

### 5.24 No test found: 5 requirements in theory_of_operation.md > Key sideloading

`f_a9133b1841ac` · severity medium

1. **Requirement** `cr_291fea02ab56`: The key manager replaces the sideloaded key when a new key is generated for HMAC without deasserting the valid indication.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:148`][134]: `the key manager replaces the sideloaded key whenever a new key is generated for HMAC, without deasserting the valid indication`

   **Verified:** Neither DV nor the firmware test replaces the key mid-operation.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56]: `// TODO(#31634): Disable sideloading for now.` — The DV forces sideload off in randomized configs.
   - [`sw/device/tests/keymgr_dpe_sideload_hmac_test.c:214-216`][135]: `// Starting HMAC with an invalid sideload key must be blocked.` — The firmware test checks the invalid-key start case, which is not one of the five items.

2. **Requirement** `cr_2bd90963e6d1`: On abort, the SHA-2 engine is disabled, clearing its internal state and the digest.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:156`][18]: `The SHA-2 engine is disabled, which clears its internal state and the digest`

   **Verified:** No test checks that the digest or SHA-2 state is cleared after an abort. The firmware test only covers a blocked start.

3. **Requirement** `cr_4fa240172006`: The key manager keeps the valid indication set for the entire duration of an operation.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:146`][136]: `The key manager has to keep it set for the entire duration of an operation`
   - [`hw/ip/hmac/doc/theory_of_operation.md:146`][136]: `because the HMAC core uses the key twice: for the inner key padding when the operation starts, and again for the outer key padding after the message has been hashed.`

   **Verified:** No test checks the valid indication across an operation. DV has sideload disabled.

4. **Requirement** `cr_562194d14c29`: CFG.key_swap has no effect on the sideloaded key.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:143`][137]: `CFG.key_swap has no effect on the sideloaded key, as the key manager delivers it in a fixed order.`

   **Verified:** The scoreboard applies key_swap only to register keys, and sideload is off in DV.

5. **Requirement** `cr_7eee2f253918`: The combined sideloaded key is muxed into the HMAC core and no copy of it is kept inside HMAC.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:139`][27]: `The combined key is muxed into the HMAC core rather than latched, so no copy of the sideloaded key is kept inside HMAC`

   **Verified:** No test checks that HMAC keeps no copy of the key.

**Searched:**

- sideload/key_valid/key_swap in the hmac DV env, scoreboard and sequences in `hw/ip/hmac/dv`: Sideload is disabled in the base vseq. The scoreboard applies key_swap only to register keys. No sideload checks.
- sideload references in hmac hjson, sv, md and core files in `hw/ip/hmac`: There is a key_sideload agent and a tb interface, but sideload is forced off. The sec_cm testplan has a sec_cm_key_sideload entry.
- HMAC sideload firmware tests in `sw/device/tests`: keymgr_dpe_sideload_hmac_test.c checks the SwInvalidConfig error for an invalid key. It does not cover key replacement, abort clearing of SHA-2 state, valid held for a whole operation, key_swap on a sideloaded key, or the absence of a key copy.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:861-862`][138]: key_swap is modelled for register keys only.

---

### 5.25 No test found: 5 requirements in theory_of_operation.md > Key sideloading

`f_efbb49af93f5` · severity medium

1. **Requirement** `cr_89cd9ce85cff`: CFG.sideload only takes effect for keyed HMAC operations, that is when CFG.hmac_en is set.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:135`][139]: `The bit only takes effect for keyed HMAC operations (CFG.hmac_en set)`

   **Verified:** Sideload is never enabled.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56] — Randomized config never sets sideload.
     ~~~
     // TODO(#31634): Disable sideloading for now.
       rand_config_value[ral.cfg.sideload.get_lsb_pos()] = 1'b0;
     ~~~

2. **Requirement** `cr_aeeb0cb3e0f0`: On abort, the message packer and the message FIFO are cleared.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:156`][18]: `the message packer and the message FIFO are cleared so that no residual message data is carried into the next operation`

   **Verified:** No abort-by-key-invalid stimulus exists.

3. **Requirement** `cr_af28a6129f5c`: The key manager deasserting the valid indication during an operation aborts that operation.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:155`][121]: `The key manager deasserting the valid indication while an operation is in progress aborts that operation`

   **Verified:** The valid signal is never driven.

4. **Requirement** `cr_c4e4f4d945ab`: CFG.sideload is ignored when only SHA-2 is computed (no key is used); it applies only to keyed HMAC operations.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:135`][139]: `it is ignored when only SHA-2 is computed, as that does not use a key.`
   - [`hw/ip/hmac/doc/programmers_guide.md:78`][140]: `This only applies to keyed HMAC operations; it is ignored when only SHA-2 is computed.`

   **Verified:** Sideload is never enabled.

5. **Requirement** `cr_d882830deff5`: HMAC does not detect a change of the sideloaded key during an operation.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:148`][134]: `Neither HMAC nor the key manager enforces this`
   - [`hw/ip/hmac/doc/theory_of_operation.md:148`][134]: `and HMAC does not detect the change.`

   **Verified:** No key change during an operation is tested.

**Searched:**

- sideload/key_valid/keymgr/SwInvalidConfig across the HMAC DV directory in `hw/ip/hmac/dv`: Only agent wiring, and sideload forced to 0; no sequence drives the agent.
- item cr_89cd9ce85cff and cr_c4e4f4d945ab: any test setting CFG.sideload with hmac_en on or off in `hw/ip/hmac/dv`: Only the forced-zero line; sideload is never set.
- item cr_aeeb0cb3e0f0 and cr_af28a6129f5c: key valid deassert during an operation, packer/FIFO clear checks in `hw/ip/hmac/dv`: No key valid driving or abort check; the agent has start_default_seq = 0 and no vseq uses it.
- item cr_d882830deff5: key change during an operation in `hw/ip/hmac/dv`: No such stimulus.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:199-219`][123]: SwInvalidConfig handling there is for other configuration errors, not for sideload.

---

### 5.26 No test found: 4 requirements in theory_of_operation.md > Performance in SHA-2 mode and HMAC mode

`f_a24982bebf63` · severity medium

1. **Requirement** `cr_2a29e638293c`: The next 16 x 32-bit message is fetched into the internal message scheduling array only after the current block completes.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:179`][141]: `This version of the IP fetches the next 16 x 32-bit message into the internal message scheduling array only after completing the current block.`

   **Verified:** No test checks the fetch-after-block-completion timing. It is only implied by the 65-cycle model.
   - [`hw/ip/hmac/dv/env/hmac_env_pkg.sv:37-39`][142]: `// 48 cycles of hashing, 16 cycles to rd next 16 words, 1 cycle to update digest` — The DV model uses 65 and 81 cycles per block, not the 80 and 96 the spec states. No exact-latency check exists.

2. **Requirement** `cr_aeb18e72aa7a`: SHA-2 256 takes 80 cycles to complete a block.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:180`][143]: `As such, it takes 80 cycles to complete a block.`

   **Verified:** The DV model uses 65 cycles per block and never checks 80. No direct latency check exists.

3. **Requirement** `cr_ba453872049c`: SHA-2 384/512 takes 96 cycles to complete a 1024-bit block.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:185`][144]: `It takes 96 cycles to complete a 1024-bit block.`

   **Verified:** The DV model uses 81 cycles for SHA-2 384/512, not 96. No check of 96 exists.

5. **Requirement** `cr_ed89d1bd86c3`: HMAC mode adds an extra 240 cycles to complete a HMAC SHA-2 256 digest.  

   - [`hw/ip/hmac/doc/theory_of_operation.md:190`][145]: `This adds an extra 240 cycles (80 for the inner key, 80 for the outer key, and 80 for the result of the first round) to complete a HMAC SHA-2 256 digest of a message.`

   **Verified:** Only the key-padding wait is modelled. The 240-cycle total HMAC overhead is never measured or checked.

Item 4 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- cycle-count checks (80, 96, 240, block cycles) in hmac dv and non-doc files in `hw/ip/hmac`: Only the env_pkg parameters (65, 81, 80 and 112 for key padding) and the scoreboard wait models. No assertion or check of the 80, 96 or 240 cycle figures.
- FIFO full / empty modelling in `hw/ip/hmac/dv/env/hmac_scoreboard.sv`: The model predicts status.fifo_full and the fifo_empty interrupt with a block-drain delay. This indirectly checks item 4.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:770-780`][146]: Models 80-cycle key padding for HMAC, but this is neither the 240-cycle total overhead nor a check of the DUT's latency.

---

### 5.27 No test found: 1 requirement in theory_of_operation.md > Performance in SHA-2 mode and HMAC mode (and 1 more section)

`f_ff609afdb8eb` · severity medium

1. **Requirement** `cr_f6528a1e1fc7`: SHA-2 256 effective throughput is 64 bytes per 80 clocks, 20% of the maximum throughput.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Performance in SHA-2 mode and HMAC mode

   - [`hw/ip/hmac/doc/theory_of_operation.md:181`][147]: ``The effective throughput considering this is `64 byte / 80 clk` or `16 clk / 80 clk`, 20% of the maximum throughput.``

   **Verified:** No test checks the 64 bytes per 80 clocks timing.
   - [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:414`][148]: `// future throughput test should check the accurate cycles` — Throughput cycles are not checked.

Item 2 of this group was not verified (see Withdrawn and Inconclusive).

**Searched:**

- throughput / 80 clk / cycle-count checks in `hw/ip/hmac`: Only the scoreboard comment saying a future throughput test is needed.
- MSG_FIFO window address stimulus and checks in `hw/ip/hmac/dv`: wr_addr is constrained to the whole window, and the scoreboard handles window writes.

---

### 5.28 No test found: 2 requirements in theory_of_operation.md > SHA-2 computation (and 2 more sections)

`f_21c95b4eed04` · severity medium

4. **Requirement** `cr_003bb8525faa`: On abort, the HMAC core returns to its idle state.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:156`][18]: `and the HMAC core returns to its idle state.`

   **Verified:** Sideload is disabled in DV and no abort test exists.
   - [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:211-212`][56]: `// TODO(#31634): Disable sideloading for now.` — Sideload is disabled in randomized config, so sideload abort and key stability are never stimulated.

5. **Requirement** `cr_01801357ac87`: The sideloaded key stays stable for the entire duration of an operation.  
   Section: hw/ip/hmac/doc/theory_of_operation.md > Theory of Operation > Design Details > Key sideloading

   - [`hw/ip/hmac/doc/theory_of_operation.md:147`][149]: `For the same reason, the key itself has to stay stable for the entire duration of an operation.`

   **Verified:** No sideload stimulus or key stability check exists, since sideload is disabled.

Items 1, 2, 3 of this group were not verified (see Withdrawn and Inconclusive).

**Searched:**

- sideload/abort/SwInvalidConfig in the DV env, tb and sva in `hw/ip/hmac/dv`: Sideload is forced to 0 in the base vseq. The sideload agent is created but has no sequence. The sva directory has only TLUL and CSR asserts.
- key_swap checks in `hw/ip/hmac/dv/env/hmac_scoreboard.sv`: The expected HMAC is computed with and without key swap.
- FIFO fetch during compression and depth limit in `hw/ip/hmac/dv`: Only a status FIFO depth/full model and coverage. No explicit check of the cited statements.

**Looked at and ruled out:**

- [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:199-219`][123]: SwInvalidConfig is modeled for other config errors, not for a sideload key becoming invalid.

---

## Inconclusive

The verifier could neither confirm nor refute these. They need a human decision.

- `f_2f63ab250dc7` (partly tested) HMAC supports key lengths of 128, 256, 384, 512 and 1024 bits. — A key-length coverpoint exists and smoke randomizes the key length, so this is partly covered, but nothing guarantees all five lengths are hit.
- `f_43010cec775f` (no test found) CFG.digest_size: the register is implemented outside the register block (true). — digest_size is exercised and checked by the scoreboard, but no test checks the hwext attribute directly.
- `f_43010cec775f` (no test found) CFG.digest_swap: the register is implemented outside the register block (true). — digest_swap is exercised and checked by the scoreboard, but no test checks the hwext attribute directly.
- `f_43010cec775f` (no test found) CFG.sha_en: hardware receives a write-qualifier pulse on software writes (true). — sha_en writes are modelled by the scoreboard, including the digest clear, but no test checks the hwqe pulse directly.
- `f_43010cec775f` (no test found) CFG.sha_en: the register is implemented outside the register block (true). — sha_en is exercised and checked by the scoreboard, but no test checks the hwext attribute directly.
- `f_81c7d089aa21` (partly tested) With SHA2_None, HMAC/SHA-2 is blocked from starting when triggered via hash_start or hash_continue. — The scoreboard does check idle state for an invalid config on the process path. I could not confirm that a hash_continue trigger is exercised.
- `f_dcd02c1afd9c` (no test found) The message length has byte granularity. — The scoreboard predicts the length as a byte count times 8, and the SAR check compares the bit length against msg size in bytes. Byte granularity is only implicitly checked.
- `f_ca4f733ef3eb` (no test found) Each message stream is finalized once by setting CMD.hash_process. — Not settled by the search.
- `f_ca4f733ef3eb` (no test found) Key registers cannot be read from SW. — No key-read check seen, but the search was incomplete.
- `f_ca4f733ef3eb` (no test found) SwPushMsgWhenDisallowed is reported when SW updates MSG_FIFO with more message words after CMD.process has been received (engine triggered to finalize). — Scoreboard predicts the error; whether stimulus pushes words after process is unconfirmed.
- `f_ca4f733ef3eb` (no test found) fifo_empty status bit is lowered for a cycle even when the Message FIFO depth stays 0. — The smoke vseq comment says the check is skipped around the empty transition, but this was not confirmed.
- `f_dbc685521d55` (no test found) While INTR_STATE.hmac_err is pending, no further error is reported in ERR_CODE. — The scoreboard models it, but I did not confirm stimulus that raises a second error while hmac_err is pending.
- `f_dbc685521d55` (no test found) Enabling sha_en locks down SW writes to the DIGEST_0..15 registers. — The scoreboard models the write lock, but I did not confirm a test that writes DIGEST while sha_en=1 and checks the read-back.
- `f_21c95b4eed04` (no test found) Software can push up to 16 entries (32 for SHA-2 384/512) to the FIFO for the next hash computation. — The scoreboard models FIFO depth and full against the 16/32 entry limit, but nothing explicitly checks pushing during compression.
- `f_21c95b4eed04` (no test found) During compression rounds the engine does not fetch data from the message FIFO. — No explicit check that the engine does not fetch from the FIFO during compression. The FIFO depth model gives only indirect coverage.
- `f_6e3c88ec1c93` (partly tested) HMAC.KEY_LENGTH.KEY_512 supports HMAC with any digest size using a 512-bit key. — Randomized only; cross coverage exists but closure is not shown.
- `f_6e3c88ec1c93` (partly tested) HMAC.KEY_LENGTH.KEY_1024 supports HMAC based on SHA-2 384 and SHA-2 512 using a 1024-bit key. — Random plus vectors; no directed guarantee.
- `f_6e3c88ec1c93` (partly tested) HMAC.KEY_LENGTH.KEY_256 supports HMAC with any digest size using a 256-bit key. — Random only.
- `f_6e3c88ec1c93` (partly tested) With Key_None, HMAC is blocked from starting when triggered via hash_start or hash_continue. — hash_start with Key_None is covered by random stimulus. The hash_continue trigger is not confirmed.
- `f_6050313b3ebc` (test plan items with no test) regwen_val_when_new_value_written_cg — It is a covergroup, not a test; no test is expected for it
- `f_6050313b3ebc` (test plan items with no test) NO_ISSUES_PENDING — It is a checklist status entry, not a testable item
- `f_704a85f8cf13` (no test found) CFG.key_swap: the register is implemented outside the register block (true). — hwext is a structural attribute; no direct check found.
- `f_704a85f8cf13` (no test found) CFG.key_swap: hardware receives a write-qualifier pulse on software writes (true). — hwqe is structural; it is only indirectly exercised through the key_swap and endian_swap tests.
- `f_b04480aeaeba` (test plan items with no test) NO_TOOL_WARNINGS_THROWN — This is a process checklist item, not a test, so there is no test to find.
- `f_b04480aeaeba` (test plan items with no test) FPV_COI_COVERAGE_AT_100 — The item is marked N/A for HMAC, so no test is expected. This is not a test gap.

## Withdrawn

Candidates the verifier refuted, with the reason. Listed so the refutations can be checked too.

- `f_b03fa03996ed` (spec too ambiguous to test) In HMAC mode, start is blocked when key_length is Key_1024 and digest_size is SHA2_256. — The programmer's guide answers the main question. ([`hw/ip/hmac/doc/programmers_guide.md:174-175`][150], [`hw/ip/hmac/doc/programmers_guide.md:186`][120])
- `f_248d8da64e01` (spec too ambiguous to test) CPU must configure digest size, secret key and key length before starting. — Other HMAC spec text answers the question. ([`hw/ip/hmac/doc/programmers_guide.md:186`][120], [`hw/ip/hmac/doc/registers.md:212`][151])
- `f_13ebd67552c8` (spec too ambiguous to test) Software may write the DIGEST registers when STATUS.hmac_idle is 1. — The programmer's guide gives the missing condition. ([`hw/ip/hmac/doc/programmers_guide.md:165-166`][152], [`hw/ip/hmac/doc/programmers_guide.md:163-164`][153])
- `f_026e7be916d9` (spec too ambiguous to test) HMAC.SECURE_WIPE wipes the internal state and secret key with a constant value provided by software. — The README and the WIPE_SECRET register description say what is wiped and with what value. ([`hw/ip/hmac/README.md:59-63`][154], [`hw/ip/hmac/data/hmac.hjson:455-457`][155])
- `f_59efb75c18ac` (spec too ambiguous to test) SwInvalidConfig is reported for an invalid digest size for SHA-2/HMAC modes. — The register description in hmac.hjson answers the question. ([`hw/ip/hmac/data/hmac.hjson:269-270`][156], [`hw/ip/hmac/data/hmac.hjson:294-295`][157])
- `f_69f49c5e5f17` (spec too ambiguous to test) SwInvalidConfig is reported for an invalid key length in HMAC mode. — The register description for CFG.key_length answers the question: in HMAC mode, Key_None (including unsupported/invalid and all-zero encodings) and Key_1024 with SHA2_256 block the start and signal an error. ([`hw/ip/hmac/doc/registers.md:203`][158], [`hw/ip/hmac/doc/registers.md:212`][151])
- `f_e96e54c35582` (spec too ambiguous to test) For SHA-2 384, only DIGEST_0..11 should be read out, the rest should be truncated out by not being read by the SW. — The DIGEST register description says that for SHA-2 384, DIGEST12..15 are truncated, irrelevant and should not be read out. ([`hw/ip/hmac/doc/registers.md:423`][159], [`hw/ip/hmac/doc/theory_of_operation.md:25`][160])
- `f_0c2b65414c0f` (spec too ambiguous to test) prim_packer compacts written data into word size and writes it to the MSG_FIFO when the write is not a full word write. — Other spec text answers most of the question. ([`hw/ip/prim/doc/prim_packer.md:24`][161], [`hw/ip/prim/doc/prim_packer.md:103`][162])
- `f_345252d6d22e` (spec contradicts itself) The row states the error is reported on a MSG_FIFO write while SHA is disabled, yet also says the code is not used in th — The row describes the nominal condition for 0x1 and then says the code is not used in the current IP, and that SwPushMsgWhenDisallowed (0x5) is reported instead. ([`hw/ip/hmac/doc/programmers_guide.md:179`][109])
- `f_1dac27e3262f` (test contradicts its own description) hmac_smoketest: plan vs. test — The plan item (chip_sw_hmac_enc) is linked to hmac_enc_test, not to hmac_smoketest. ([`hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson:19-21`][163])
- `f_f6592776203d` (test contradicts its own description) hmac_error_conditions_test: comment vs. code — The programmer's guide says SwPushMsgWhenShaDisabled (0x1) is not used in the current IP. ([`hw/ip/hmac/doc/programmers_guide.md:179`][109])
- `f_349d3e01b978` (test contradicts its own description) keymgr_dpe_sideload_hmac_test: plan vs. test — The repository does not link the HMAC plan item to this test. ([`hw/ip/hmac/data/hmac_sec_cm_testplan.hjson:34-36`][164])
- `f_3747f22ce1b8` (test contradicts its own description) check_error_code: comment vs. code — The write-back exists at line 543: csr_wr writes error_code, which was just read from intr_state, back to clear it. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:541-544`][165])
- `f_93f9ac580c2e` (test contradicts its own description) hmac_scoreboard::run_phase: comment vs. code — The code does wait for reset assertion (line 88) and then for release (line 90, at the top of the forever loop). ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:88-90`][166])
- `f_8f147aa0fc29` (spec not tested) Partially tested: 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > CFG — The hmac DV environment covers all five items. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:359-373`][167], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:157-163`][168])
- `f_fb595bd4fab8` (spec not tested) 5 plan item(s) with no implementing test — All five items are false positives. ([`hw/ip/hmac/dv/sva/hmac_bind.sv:7-14`][169], [`hw/dv/tools/dvsim/tests/tl_access_tests.hjson:20-23`][170])
- `f_45c6516939b2` (spec not tested) Planned but not implemented: 3 requirement(s) in hw/ip/hmac/README.md > Overview > Features … (2 sections) — The HMAC DV environment has stimulus and failing checks for all three items. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85], [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:449-451`][171])
- `f_b8a20e11b122` (spec not tested) Only weak evidence (non-failing checks, coverage, list entries): 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > CFG — The randomized smoke and test-vector vseqs drive key_length, digest_size and key_swap, including illegal values. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:861-862`][138], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- `f_2b35e2fe8b98` (spec not tested) Untested: 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > CFG … (2 sections) — The register attributes hwext/hwqe are exercised functionally. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:159-161`][172], [`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- `f_3a7bacacffd0` (spec not tested) Untested: 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > CMD … (2 sections) — All five items are register-implementation attributes (hwext/hwqe). ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:383-387`][174])
- `f_76363c75b0ec` (spec not tested) Untested: 2 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > MSG_LENGTH_UPPER — Both items are exercised by the HMAC DV environment. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:700-707`][93], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:1035-1038`][175])
- `f_053c34dadee9` (spec not tested) Planned but not implemented: 2 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > STATUS — The scoreboard predicts and checks every STATUS read, covering fifo_empty and fifo_depth. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:383-387`][174], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:510-514`][176])
- `f_7cfb3c92427a` (spec not tested) Untested: 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > STATUS … (3 sections) — All five items are hwext/hwqe implementation attributes, and the existing HMAC DV env checks the behavior they imply. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:554-557`][177], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:496`][31])
- `f_96e3b895485b` (spec not tested) Planned but not implemented: 5 requirement(s) in hw/ip/hmac/data/hmac.hjson > hmac > countermeasures … (3 sections) — All five items are checked by existing DV. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:297-299`][178])
- `f_a987ffa7ad7f` (spec not tested) Untested: 2 requirement(s) in hw/ip/hmac/doc/interfaces.md > Hardware Interfaces > Interrupts — Both interrupts are checked by the HMAC DV scoreboard on every intr_state read, and the stock intr_test suite is included. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:391-392`][179], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:693-694`][180])
- `f_875cca641e48` (spec not tested) Planned but not implemented: 1 requirement(s) in hw/ip/hmac/doc/interfaces.md > Hardware Interfaces > Security Alerts — The hmac_tl_intg_err test exists. ([`hw/dv/tools/dvsim/tests/tl_access_tests.hjson:18-24`][181], [`hw/dv/sv/cip_lib/seq_lib/cip_base_vseq__tl_errors.svh:478`][182])
- `f_11cae0e33fc2` (spec not tested) Planned but not implemented: 5 requirement(s) in hw/ip/hmac/doc/interfaces.md > Hardware Interfaces … (3 sections) — All five items are covered by the DV environment even though the testplan entries are only "planned". ([`hw/ip/hmac/dv/sva/hmac_bind.sv:7-14`][169], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:690-697`][183])
- `f_f0277d634375` (spec not tested) Planned but not implemented: 3 requirement(s) in hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > FIFO Depth and Empty status — The HMAC DV scoreboard checks all three behaviours on every status and intr_state read, and the smoke and related vseqs create the stimulus. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:510-514`][176], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:683`][184])
- `f_872724e2676a` (spec not tested) Planned but not implemented: 5 requirement(s) in hw/ip/hmac/doc/programmers_guide.md > Programmer's Guide > Updating the configurations … (3 sections) — The finding treats these items as plan-only, but the HMAC DV environment implements them. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:398-399`][185], [`hw/ip/hmac/dv/env/hmac_scoreboard.sv:319-325`][186])
- `f_2fc38343b1ac` (spec not tested) Register reset values: no suite covers every register — A shared generic CSR test library provides csr_hw_reset and csr_mem_rw_with_rand_reset, enabled per block via csr_tests_mode. ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:21-23`][187], [`hw/dv/sv/csr_utils/csr_hw_reset_seq.sv:47-51`][188])
- `f_e684b2415bb9` (spec not tested) Register access: no suite covers every register — The finding does not name its block. ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:29-40`][189], [`hw/ip/hmac/dv/hmac_sim_cfg.hjson:30`][190])

Withdrawn items from the groups in section 5 (92):

- For writes within the MSG_FIFO window, only address bits [1:0] are used to determine the write offset (to handle non-32-bit writes). — The base vseq randomizes the write address across the window and uses partial contiguous byte masks. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:103-112`][191])
- SwInvalidConfig is reported for an invalid key length in HMAC mode. — hmac_smoke randomizes illegal key lengths, which become Key_None. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-118`][192])
- prim_packer compacts written data into word size and writes it to the MSG_FIFO when the write is not a full word write. — The DV vseq randomizes the byte mask and address, so partial writes are issued. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:358`][193])
- HMAC supports key lengths of 128/256/384/512/1024 bits provided the key length does not exceed the digest block size (max 512-bit key for SHA-2 256). — Random key-length and digest-size sweep, with SHA2_256 plus Key_1024 predicted invalid by the scoreboard and checked for SwInvalidConfig, plus key-length/digest coverage. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- csr_rw — hmac_csr_rw runs via the imported csr_tests.hjson. ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:29`][194])
- alert_test — hmac_alert_test is run via the imported alert_test.hjson. ([`hw/dv/tools/dvsim/tests/alert_test.hjson:13`][195])
- regwen_csr_and_corresponding_lockable_csr — The testplan maps it to csr_rw and csr_aliasing, which run for hmac. ([`hw/dv/tools/dvsim/testplans/csr_testplan.hjson:100`][196])
- csr_aliasing — The generated hmac_csr_aliasing test exists through the csr_tests.hjson import. ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:45-47`][197])
- fifo_empty_status_interrupt — The plan item lists hmac_smoke and other tests, and the scoreboard checks the fifo_empty interrupt. ([`hw/ip/hmac/data/hmac_testplan.hjson:113-120`][198])
- key_length 6'b00_1000 selects a 512-bit key. — Randomized smoke covers Key_512 and the CFG read-back is checked. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-125`][199])
- Invalid or unsupported digest_size values are mapped to SHA2_None. — Smoke randomizes illegal digest_size values, writes them, and reads back against a scoreboard prediction of SHA2_None. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-125`][199])
- key_length 6'b00_0001 selects a 128-bit key. — One-hot randomized key_length covers Key_128, the key_length read-back is checked, and the digest is checked. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-125`][199])
- key_length 6'b01_0000 selects a 1024-bit key. — The scoreboard computes an HMAC with a 1024-bit key via the DPI model and compares it with the DUT digest. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:497-498`][200])
- The key length determines how many KEY registers are relevant for the HMAC operation. — The expected digest is computed with the configured key_length, and the scoreboard compares it with the DUT digest. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:497-498`][200])
- DIGEST.digest: hardware receives a write-qualifier pulse on software writes (true). — Digest writes in the SAR flow are predicted by the scoreboard, and the restored digest is compared on later reads. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:336-340`][201])
- MSG_LENGTH_LOWER.v: the register is implemented outside the register block (true). — The length register is written and read back, and the DV_CHECK_EQ at line 726 fails if it is wrong. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:726`][202])
- MSG_LENGTH_UPPER.v: hardware receives a write-qualifier pulse on software writes (true). — msg_length_upper is written with toggling 32-bit upper values and the result is checked at line 726. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:726`][202])
- CFG.digest_swap: hardware receives a write-qualifier pulse on software writes (true). — digest_swap is randomized in the sequences and the scoreboard flips the expected digest by it, so the write path is exercised. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:165`][203])
- CFG.key_length: hardware receives a write-qualifier pulse on software writes (true). — key_length is randomized and read back, and the scoreboard checks the resulting HMAC digests, so the write path is exercised. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:168`][204])
- The digest is cleared when the SHA-2 engine is disabled. — The smoke vseq disables sha_en and the scoreboard sets the expected digest to zero. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:307`][205])
- The digest is cleared when CFG.sha_en transitions from 1 to 0. — The scoreboard clears exp_digest on sha_en 1->0, and later digest reads are compared against it. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:306-307`][206])
- Once the FIFO is full, software cannot push more data until the current block is processed. — The scoreboard predicts FIFO fullness, with drain after block processing, and compares it with the DUT's status and the FIFO-empty interrupt. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:821-825`][207])
- MSG_FIFO occupies a wide address range rather than a single 4-byte address. — Random writes across the full window are scoreboarded and the resulting digest is checked. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:104`][208])
- The byte order of the key registers is big-endian by default. — HMAC scoreboard computes the expected digest with the key kept big-endian when key_swap=0, and key_swap is randomized including 0. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:860-869`][209])
- With Key_None, an error is signalled to SW when start is attempted. — Random smoke hits Key_None with hmac_en, and the scoreboard checks the error and idle prediction. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- Unsupported, invalid and all-zero key_length values are mapped to Key_None (6'b10_0000). — Illegal and zero key_length are randomized and the Key_None mapping is predicted and checked on read. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:359-373`][167])
- key_length 6'b00_0010 selects a 256-bit key. — Random key_length includes Key_256 (the default is 6'b000010). ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:359-373`][167])
- Hardware byte swaps for endian_swap are performed at TL-UL word granularity. — Random sub-word writes with endian_swap are checked against a per-word swap model. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:157-163`][168])
- Unsupported, invalid and all-zero digest_size values are mapped to SHA2_None (4'b1000). — Illegal digest_size is randomized and the SHA2_None mapping is predicted. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:359-373`][167])
- When endian_swap is 1, each multi-byte value written to MSG_FIFO, regardless of alignment, is added to the message in big-endian byte order. — Unaligned and sub-word writes are randomized and the scoreboard models big-endian order for them. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:157-163`][168])
- fifo_empty indicates the FIFO is empty. — fifo_empty is predicted in hmac_status_data and compared exactly by DV_CHECK_EQ on every STATUS read. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:383-387`][174])
- STATUS.fifo_depth: the register is implemented outside the register block (true). — fifo_depth is checked against the model on every STATUS read and raises uvm_error on mismatch. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:510-514`][176])
- The hmac module has a TL-UL bus device interface named tl. — tlul_assert is bound to the tl device port. ([`hw/ip/hmac/dv/sva/hmac_bind.sv:7-14`][169])
- The tl port is a req_rsp interface of type tlul_pkg::tl acting as responder with width 1. — tlul_assert checks the device-side TL-UL protocol on tl_i/tl_o. ([`hw/ip/hmac/dv/sva/hmac_bind.sv:7-14`][169])
- The fifo_empty interrupt is raised only if the message FIFO has previously been full. — The scoreboard predicts the interrupt only after the FIFO was full, and checks intr_state against the prediction. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:690-697`][183])
- The fifo_empty interrupt is not raised once software has written the Process or Stop command to finish the hashing operation. — The scoreboard clears fifo_full_detected on process/stop, so no interrupt is predicted afterwards, and this is checked on reads. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:690-697`][183])
- The fifo_empty interrupt indicates that the message FIFO is empty. — The scoreboard predicts the interrupt on FIFO empty and compares it with the DUT's intr_state. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:690-697`][183])
- CFG.endian_swap: hardware receives a write-qualifier pulse on software writes (true). — The scoreboard models endian_swap, and the digest comparison fails the test if the DUT behaves differently. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:159-161`][172])
- CMD.hash_stop: hardware receives a write-qualifier pulse on software writes (true). — trigger_hash_stop is driven by the sequences, and the scoreboard checks the results. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_process: the register is implemented outside the register block (true). — hash_process is exercised by every hash operation, and the digest is checked. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_continue: the register is implemented outside the register block (true). — trigger_hash_continue is exercised by the sequences, and the scoreboard checks the resumed hash. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_start: the register is implemented outside the register block (true). — hash_start is exercised by every test, and the scoreboard models it (hmac_start). ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_continue: hardware receives a write-qualifier pulse on software writes (true). — hash_continue written in trigger_hash_continue; digest checked after. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_process: hardware receives a write-qualifier pulse on software writes (true). — hash_process is written in every hash flow; the scoreboard checks hmac_done and the digest. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_start: hardware receives a write-qualifier pulse on software writes (true). — hash_start is written in every hash flow, and the digest checks fail if the write pulse is missing. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- CMD.hash_stop: the register is implemented outside the register block (true). — hash_stop is written in trigger_hash_stop, and the later context-switch flow is checked via digests. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:233-251`][173])
- STATUS.fifo_empty: the register is implemented outside the register block (true). — The scoreboard predicts STATUS including fifo_empty and checks it on reads. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:383-387`][174])
- The message FIFO buffer is 32 entries deep and 32 bits wide. — The write sequence waits on status.fifo_depth against a depth of 32, and the scoreboard checks fifo_depth and fifo_full reads. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:449-451`][171])
- In HMAC mode the secret key length is configurable up to 1024 bits. — Random configs and test vectors cover key lengths up to 1024 in HMAC mode, and the scoreboard compares the digest. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:804-807`][210])
- 1024-bit keys are not supported for SHA-2 256 (block size 512-bit). — The scoreboard predicts the invalid config for SHA-2 256 with a 1024-bit key, and the vseqs drive that combination. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- csr_hw_reset — hmac_csr_hw_reset exists via csr_tests.hjson ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:20-24`][211])
- back_pressure — hmac_back_pressure test with zero_delays exists ([`hw/ip/hmac/dv/hmac_sim_cfg.hjson:73-75`][212])
- csr_mem_rw_with_rand_reset — hmac_csr_mem_rw_with_rand_reset exists via csr_tests.hjson ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:61-63`][213])
- When sha_en is 0, the SHA engine does not initiate compression. — Smoke vseq runs with sha_en=0 and the scoreboard checks the resulting behaviour. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:147-148`][214])
- With key_swap 0, KEY_* CSRs use big-endian representation. — Scoreboard models key_swap=0 as big-endian, and the digest comparison fails if the RTL differs. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:861-870`][215])
- A message written to MSG_FIFO one byte at a time is not affected by endian_swap. — Scoreboard handles masked byte writes and compares the digest. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:157-163`][168])
- MSG_LENGTH_UPPER.v: the register is implemented outside the register block (true). — The register is read and written in tests, and the scoreboard predicts and checks its value. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:700-707`][93])
- For SHA-2 384/512 the message length is 128 bits, with the upper 64 bits zero-padded: {32'b0, 32'b0, MSG_LENGTH_UPPER, MSG_LENGTH_LOWER}. — Every SHA-384/512 digest is compared against the DPI model and the NIST vectors, which only match if the upper 64 length bits are zero. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:700-707`][93])
- STATUS.fifo_full: the register is implemented outside the register block (true). — The scoreboard predicts fifo_full and compares every STATUS read, so the externally implemented register is checked. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:554-557`][177])
- STATUS.hmac_idle: the register is implemented outside the register block (true). — The idle pin is checked against the model and STATUS reads are compared, so hmac_idle is covered. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:554-557`][177])
- KEY.key: hardware receives a write-qualifier pulse on software writes (true). — Key writes go through the hwqe pulse to the HMAC core. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:496`][31])
- KEY.key: the register is implemented outside the register block (true). — The externally implemented key registers are exercised end to end, because the digest depends on the key and is compared. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:496`][31])
- DIGEST.digest: the register is implemented outside the register block (true). — DIGEST reads are compared with the predicted digest. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:496`][31])
- Attempts to update CFG or KEY while processing are discarded. — Random CFG/KEY writes during hash, discarded in scoreboard. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:398-399`][185])
- CFG and KEY registers cannot be updated while the engine is processing messages. — Same stimulus and model. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:398-399`][185])
- KEY register ignores any attempt to access the secret key during processing. — KEY write during hash discarded and flagged. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:319-325`][186])
- For keyed HMAC, the values of KEY_0..X must also be maintained and restored as part of the saved context, where X is the last register used for the key length (e.g. X=7 for HMAC-256). — S&R in the base vseq keeps key_bak and probes secret_key; digest checked by the scoreboard. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:398-399`][185])
- SwPushMsgWhenDisallowed is reported when SW pushes message words before the engine has been triggered to start. — Scoreboard expects the error for pushes before start. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:147-148`][214])
- The fatal_fault alert is triggered when a fatal TL-UL bus integrity fault is detected. — The common hmac_tl_intg_err test injects integrity errors and checks that the fatal alert fires and that the status CSR field is set. ([`hw/dv/tools/dvsim/tests/tl_access_tests.hjson:18-24`][181])
- The HMAC implements an end-to-end bus integrity scheme as the BUS.INTEGRITY countermeasure (HMAC.BUS.INTEGRITY). — Shared sec_cm tests are imported, and the testplan maps BUS.INTEGRITY to hmac_tl_intg_err. ([`hw/ip/hmac/dv/hmac_sim_cfg.hjson:34`][216])
- CFG is updated only when the engine is idle. — The scoreboard ignores a CFG write while a hash is active, and CFG read checks would fail if the DUT updated it. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:297-299`][178])
- In HMAC mode, start is blocked when key_length is Key_1024 and digest_size is SHA2_256. — The scoreboard predicts the error for the Key_1024/SHA2_256 HMAC combination, and the sequences generate it. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- A software write to CFG while the engine is computing a hash is discarded. — Same CFG-discard modelling in the scoreboard, plus coverage on the discard path. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:297-299`][178])
- STATUS.fifo_full indicates that the Message FIFO is full. — The scoreboard predicts STATUS including fifo_full and compares it on reads. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:385`][217])
- hmac_err is an Event-type interrupt. — hmac_err is raised by error stimulus, and the scoreboard predicts and checks intr_state.hmac_err. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:1048`][218])
- fifo_empty is a Status-type interrupt. — The scoreboard models the spec conditions for fifo_empty and checks intr_state.fifo_empty on every read. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:391-392`][179])
- Unknown checks on DUT outputs — ASSERT_KNOWN assertions in hmac.sv check this in every simulation. ([`hw/ip/hmac/rtl/hmac.sv:1087-1088`][219])
- tl_intg_err — hmac_tl_intg_err comes from the imported common tl_access_tests list. ([`hw/dv/tools/dvsim/tests/tl_access_tests.hjson:20-23`][170])
- csr_bit_bash — hmac_csr_bit_bash comes from the imported common csr_tests list. ([`hw/dv/tools/dvsim/tests/csr_tests.hjson:37-39`][220])
- key_length is a 6-bit one-hot encoded field configuring the HMAC key length. — Random one-hot and illegal key_length values are driven. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-118`][192])
- key_length 6'b00_0100 selects a 384-bit key. — The scoreboard computes the HMAC with Key_384 through the DPI model and compares it with the real digest. ([`hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv:114-118`][192])
- When CFG.key_swap is 1, the endianness of each KEY_* register is swapped (to little endian). — The smoke vseq randomizes key_swap. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:861-862`][138])
- digest_size is a 4-bit one-hot encoded field selecting the digest size for HMAC or SHA-2. — digest_size is randomized over one-hot and illegal values. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:366-372`][221])
- The HMAC key length must not exceed the block size of the configured SHA-2 digest: at most 1024 bits for SHA-2 384/512 and at most 512 bits for SHA-2 256. — The scoreboard flags SHA2_256 with Key_1024 as an invalid configuration. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:173-177`][85])
- STATUS.fifo_depth reports the number of entry slots currently occupied in the FIFO (the FIFO entry count). — Scoreboard compares read fifo_depth to the model on every status read. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:510-514`][176])
- The fifo_empty interrupt is raised only if the message FIFO is actually writable by software (all listed conditions are met). — Scoreboard models the interrupt conditions and checks intr_state. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:683`][184])
- The fifo_empty status interrupt fires when the Message FIFO runs empty after the SHA-2 engine resumes popping data. — The model raises the interrupt when the FIFO empties after being full, and intr_state reads are checked. ([`hw/ip/hmac/dv/env/hmac_scoreboard.sv:683`][184])
- FPV_ASSERTIONS_PROVEN_AT_V3 — Checklist item marked N/A, not something a test implements. ([`hw/ip/hmac/doc/checklist.md:245`][222])
- DESIGN_DELTAS_CAPTURED_V3 — Documentation checklist item marked Done, not a test item. ([`hw/ip/hmac/doc/checklist.md:243`][223])
- TLUL assertions — tlul_assert is bound in hmac_bind.sv, so protocol checks run in every sim. ([`hw/ip/hmac/dv/sva/hmac_bind.sv:7-14`][169])
- SIM_NIGHTLY_REGRESSION_AT_V3 — Regression-process checklist item, documented in an issue, not a test item. ([`hw/ip/hmac/doc/checklist.md:246`][224])
- sec_cm_bus_integrity — hmac_tl_intg_err comes from the shared tl_access_tests.hjson that HMAC imports. ([`hw/dv/tools/dvsim/tests/tl_access_tests.hjson:20-23`][170])

## Run record

| Stage | Model |
|---|---|
| Spec extraction | claude-sonnet-5-5 |
| Test extraction | claude-sonnet-5-5 |
| Merge and link | claude-sonnet-5-5 |
| Correlate | claude-sonnet-5-5 |
| Analyze | claude-opus-5-5, claude-sonnet-5-5 |
| Verify | claude-sonnet-5-5 |

This run: $0.00 in 20 s (cached results reused where the inputs were unchanged). 
All LLM spend recorded for this workspace: $33.53.

Manifest `work/opentitan-hmac/manifest.yaml` (sha256 `79bf9387ac9e`); findings `work/opentitan-hmac/findings.yaml`; verifier transcripts in `work/opentitan-hmac/verify_runs/<finding id>/`.

[1]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L59
[2]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L454-L456
[3]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L60
[4]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L311
[5]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L125
[6]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L234
[7]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L123
[8]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L197-L198
[9]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L504
[10]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L44
[11]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L43
[12]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L503
[13]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L161
[14]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L529
[15]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L59-L77
[16]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L417
[17]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L418
[18]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L156
[19]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L422-L423
[20]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L110
[21]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L154
[22]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L123
[23]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L155-L157
[24]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L102
[25]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L164
[26]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L152-L157
[27]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L139
[28]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L42
[29]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_test_vectors_sha_vseq.sv#L61
[30]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_test_vectors_sha_vseq.sv#L137
[31]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L496
[32]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L41
[33]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/hmac_enc_test.c#L98
[34]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson#L13-L14
[35]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/hmac_enc_test.c#L111
[36]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/hmac_enc_test.c#L61-L70
[37]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson
[38]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/crypto/hmac_multistream_functest.c#L109
[39]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L482-L484
[40]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L40
[41]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L62
[42]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L61
[43]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L455
[44]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_cov.sv#L71-L87
[45]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L355
[46]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L429
[47]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L207
[48]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L717-L718
[49]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L456
[50]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L482
[51]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L398
[52]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_cov.sv#L101
[53]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L484
[54]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L80
[55]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L45
[56]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L211-L212
[57]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L481
[58]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L216-L220
[59]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L507
[60]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L545
[61]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L113
[62]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L157
[63]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L112
[64]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L82
[65]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L141
[66]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/keymgr_dpe_sideload_hmac_test.c#L52-L55
[67]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/keymgr_dpe_sideload_hmac_test.c#L142-L144
[68]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L198-L219
[69]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L34
[70]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L217-L219
[71]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_back_pressure_vseq.sv#L8-L18
[72]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/testplans/tl_device_access_types_testplan.hjson#L21
[73]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L247-L249
[74]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L244
[75]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L251
[76]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L254
[77]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L63
[78]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L457
[79]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L482-L483
[80]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L491
[81]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/hmac_secure_wipe_test.c#L51-L55
[82]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L203
[83]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L353
[84]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L315
[85]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L173-L177
[86]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/rtl/hmac_reg_top.sv#L643
[87]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L202
[88]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L162-L169
[89]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L396
[90]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L413-L417
[91]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L397
[92]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L476-L484
[93]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L700-L707
[94]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L522-L541
[95]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L70
[96]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L24
[97]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L693
[98]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L211
[99]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_cov.sv#L100
[100]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L8
[101]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L5
[102]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L9
[103]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L6
[104]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/interfaces.md#L23
[105]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L416
[106]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L144
[107]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L554-L556
[108]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L239
[109]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L179
[110]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_cov.sv#L109
[111]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L42
[112]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L41
[113]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L111
[114]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/rtl/hmac.sv#L1061-L1062
[115]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L125
[116]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L107
[117]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_cov.sv#L96-L101
[118]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L77
[119]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L127
[120]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L186
[121]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L155
[122]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L106
[123]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L199-L219
[124]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/top_earlgrey/dv/env/seq_lib/chip_sw_keymgr_dpe_sideload_hmac_vseq.sv#L1
[125]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L116
[126]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L1041-L1056
[127]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L17
[128]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L7
[129]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/cov/hmac_unr_excl.el#L13
[130]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L57
[131]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L56
[132]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L83
[133]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_smoke_vseq.sv#L211
[134]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L148
[135]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/sw/device/tests/keymgr_dpe_sideload_hmac_test.c#L214-L216
[136]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L146
[137]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L143
[138]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L861-L862
[139]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L135
[140]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L78
[141]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L179
[142]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_env_pkg.sv#L37-L39
[143]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L180
[144]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L185
[145]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L190
[146]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L770-L780
[147]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L181
[148]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L414
[149]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L147
[150]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L174-L175
[151]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L212
[152]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L165-L166
[153]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/programmers_guide.md#L163-L164
[154]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/README.md#L59-L63
[155]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L455-L457
[156]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L269-L270
[157]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac.hjson#L294-L295
[158]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L203
[159]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/registers.md#L423
[160]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/theory_of_operation.md#L25
[161]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/prim/doc/prim_packer.md#L24
[162]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/prim/doc/prim_packer.md#L103
[163]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/top_earlgrey/data/ip/chip_hmac_testplan.hjson#L19-L21
[164]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac_sec_cm_testplan.hjson#L34-L36
[165]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L541-L544
[166]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L88-L90
[167]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L359-L373
[168]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L157-L163
[169]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/sva/hmac_bind.sv#L7-L14
[170]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/tl_access_tests.hjson#L20-L23
[171]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L449-L451
[172]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L159-L161
[173]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L233-L251
[174]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L383-L387
[175]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L1035-L1038
[176]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L510-L514
[177]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L554-L557
[178]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L297-L299
[179]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L391-L392
[180]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L693-L694
[181]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/tl_access_tests.hjson#L18-L24
[182]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/sv/cip_lib/seq_lib/cip_base_vseq__tl_errors.svh#L478
[183]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L690-L697
[184]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L683
[185]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L398-L399
[186]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L319-L325
[187]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L21-L23
[188]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/sv/csr_utils/csr_hw_reset_seq.sv#L47-L51
[189]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L29-L40
[190]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/hmac_sim_cfg.hjson#L30
[191]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L103-L112
[192]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L114-L118
[193]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L358
[194]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L29
[195]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/alert_test.hjson#L13
[196]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/testplans/csr_testplan.hjson#L100
[197]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L45-L47
[198]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/data/hmac_testplan.hjson#L113-L120
[199]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L114-L125
[200]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L497-L498
[201]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L336-L340
[202]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L726
[203]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L165
[204]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L168
[205]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L307
[206]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L306-L307
[207]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L821-L825
[208]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L104
[209]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L860-L869
[210]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/seq_lib/hmac_base_vseq.sv#L804-L807
[211]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L20-L24
[212]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/hmac_sim_cfg.hjson#L73-L75
[213]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L61-L63
[214]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L147-L148
[215]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L861-L870
[216]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/hmac_sim_cfg.hjson#L34
[217]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L385
[218]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L1048
[219]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/rtl/hmac.sv#L1087-L1088
[220]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/dv/tools/dvsim/tests/csr_tests.hjson#L37-L39
[221]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/dv/env/hmac_scoreboard.sv#L366-L372
[222]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L245
[223]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L243
[224]: https://github.com/lowRISC/opentitan/blob/b84154c71810e853c0b37c00e96bb830804dd9f4/hw/ip/hmac/doc/checklist.md#L246
