import Std

/-!
One execution's pytest phase reduction and its contribution to status counts.
The snapshot has at most one call report; source/project/run identity grouping,
JSON validation, exit-code policy and browser observations are outside it.
-/

namespace BrowserTestKit

inductive Status where
  | passed | failed | error | interrupted | blocked | unavailable | skipped
  deriving DecidableEq, Repr

structure Phases where
  setup : Option Status
  call : Option (Option Status)
  teardown : Option Status
  deriving Repr

-- call = none means no call report. some none means a report without status.
def reducePhases (phases : Phases) : Option Status :=
  if phases.call = some (some .failed) then some .failed
  else if phases.setup = some .error ∨ phases.teardown = some .error then some .error
  else match phases.call with
    | some status => status
    | none => phases.setup.orElse (fun _ => phases.teardown)

inductive Evidence where
  | test (phases : Phases)
  | direct (status : Option Status)
  | summary (status : Option Status)

def contribution : Evidence → Option Status
  | .test phases => reducePhases phases
  | .direct status => status
  | .summary _ => none

def countStatus (wanted : Status) : List Evidence → Nat
  | [] => 0
  | event :: rest => (if contribution event = some wanted then 1 else 0) + countStatus wanted rest

/-- Cleanup errors never replace a recorded call failure with another status. -/
theorem call_failure_priority (setup teardown : Option Status) :
    reducePhases ⟨setup, some (some .failed), teardown⟩ = some .failed := by
  simp [reducePhases]

/-- Setup/teardown errors cannot turn into passed, including after a passing call. -/
theorem phase_error_not_passed (phases : Phases)
    (error : phases.setup = some .error ∨ phases.teardown = some .error) :
    reducePhases phases ≠ some .passed := by
  simp only [reducePhases]
  split
  · simp
  · simp_all

/-- A passed result needs an actual passed phase; missing/blocked data cannot invent it. -/
theorem passed_requires_passed_phase (phases : Phases)
    (passed : reducePhases phases = some .passed) :
    phases.setup = some .passed ∨ phases.call = some (some .passed) ∨
      phases.teardown = some .passed := by
  unfold reducePhases at passed
  split at passed
  · cases passed
  · split at passed
    · cases passed
    · cases call : phases.call with
      | some status =>
        simp only [call] at passed
        exact Or.inr (Or.inl (by simp [passed]))
      | none =>
        simp only [call] at passed
        cases setup : phases.setup with
        | some status =>
          simp only [setup, Option.orElse_some] at passed
          exact Or.inl passed
        | none =>
          simp only [setup, Option.orElse_none] at passed
          exact Or.inr (Or.inr passed)

/-- No observed status remains no status, whether the call report is absent or empty. -/
theorem missing_status_stays_missing :
    reducePhases ⟨none, none, none⟩ = none ∧
      reducePhases ⟨none, some none, none⟩ = none := by
  simp [reducePhases]

/-- A session rollup adds no per-test count at any position in the stream. -/
theorem session_summary_count_neutral (wanted : Status) (status : Option Status)
    (before after : List Evidence) :
    countStatus wanted (before ++ .summary status :: after) =
      countStatus wanted (before ++ after) := by
  induction before with
  | nil => simp [countStatus, contribution]
  | cons event rest ih => simp [countStatus, ih]

/-- Failed call plus broken teardown contributes one failed test and no extra error. -/
theorem failed_call_counted_once (setup : Option Status) (rest : List Evidence) :
    countStatus .failed (.test ⟨setup, some (some .failed), some .error⟩ :: rest) =
      1 + countStatus .failed rest ∧
    countStatus .error (.test ⟨setup, some (some .failed), some .error⟩ :: rest) =
      countStatus .error rest := by
  simp [countStatus, contribution, call_failure_priority]

end BrowserTestKit
