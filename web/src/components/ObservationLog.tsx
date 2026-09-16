/**
 * The observation log (PLAN.md §5).
 *
 * The key UX move, in the spec's words: pre-populate the log from that night's
 * computed target list — checkboxes next to everything recommended, plus
 * free-text add for anything else. Nobody should have to retype what the
 * planner just told them to look at.
 */

import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type LogPrefillResponse,
  type SessionModel,
} from "../api";
import { formatTime, scoreColor } from "../format";

interface Props {
  date: string;
  locationKey: string;
  timeZone: string;
}

export function ObservationLog({ date, locationKey, timeZone }: Props) {
  const [prefill, setPrefill] = useState<LogPrefillResponse | null>(null);
  const [session, setSession] = useState<SessionModel | null>(null);
  const [sessions, setSessions] = useState<SessionModel[]>([]);
  const [freeText, setFreeText] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    Promise.all([
      api.logPrefill(date, locationKey, 6),
      api.sessions(10),
    ])
      .then(([prefillData, sessionList]) => {
        setPrefill(prefillData);
        setSessions(sessionList);
        // Reattach to an existing session for this night rather than
        // silently creating a duplicate.
        const existing = sessionList.find(
          (s) => s.date === date && s.location_key === locationKey,
        );
        setSession(existing ?? null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [open, date, locationKey]);

  async function startSession() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSession({
        date,
        location: locationKey,
        notes: notes || undefined,
      });
      setSession(created);
      setSessions([created, ...sessions]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function logObject(objectId: string | null, objectName: string) {
    if (!session?.id) return;
    setBusy(true);
    try {
      await api.addObservation(session.id, {
        object_id: objectId ?? undefined,
        object_name: objectName,
        observed_at_utc: new Date().toISOString(),
      });
      setSession(await api.session(session.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeObservation(id: number) {
    if (!session?.id) return;
    setBusy(true);
    try {
      await api.deleteObservation(id);
      setSession(await api.session(session.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const loggedIds = new Set(
    (session?.observations ?? []).map((o) => o.object_id).filter(Boolean),
  );

  return (
    <section className="panel">
      <h2 className="panel-heading">
        <button
          className="group-head"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
        <span className="caret">{open ? "▾" : "▸"}</span>
        Observation log
        <span className="muted">
          {session
            ? `session ${session.id} · ${session.observations.length} logged`
            : "not started for this night"}
          </span>
        </button>
      </h2>

      {open && (
        <>
          {error && <p className="warning">{error}</p>}

          {!session && (
            <div className="log-start">
              <input
                type="text"
                placeholder="Session notes (optional)"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
              <button className="secondary" onClick={startSession} disabled={busy}>
                Start session
              </button>
              <p className="muted small">
                Starting a session freezes tonight's conditions onto it, so the
                entry still means something after the forecast expires.
              </p>
            </div>
          )}

          {session && (
            <>
              {session.conditions && (
                <p className="note">
                  Conditions frozen at session start:{" "}
                  {session.conditions.is_gradeable ? (
                    <>
                      deep-sky {Math.round(session.conditions.deep_sky_peak)} (
                      {session.conditions.deep_sky_grade}),{" "}
                    </>
                  ) : (
                    <>no weather that night, so no grade was recorded; </>
                  )}
                  {Number(session.conditions.dark_hours).toFixed(1)} h true dark,
                  moon {Math.round(Number(session.conditions.moon_illumination) * 100)}%
                </p>
              )}

              <h4>Tonight's recommendations</h4>
              <div className="log-candidates">
                {(prefill?.candidates ?? []).map((candidate) => {
                  const done = loggedIds.has(candidate.object_id);
                  return (
                    <button
                      key={candidate.object_id}
                      className={`log-chip ${done ? "log-chip-done" : ""}`}
                      disabled={busy || done}
                      onClick={() =>
                        logObject(candidate.object_id, candidate.object_name)
                      }
                      title={
                        done
                          ? "Already logged this session"
                          : `Log ${candidate.object_name}`
                      }
                    >
                      <span
                        className="log-chip-score"
                        style={{ background: scoreColor(candidate.score) }}
                      >
                        {Math.round(candidate.score)}
                      </span>
                      {candidate.object_name}
                      {candidate.already_logged && !done && (
                        <span className="muted"> · seen before</span>
                      )}
                      {done && " ✓"}
                    </button>
                  );
                })}
              </div>

              <div className="log-freetext">
                <input
                  type="text"
                  placeholder="Anything else you saw…"
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && freeText.trim()) {
                      void logObject(null, freeText.trim());
                      setFreeText("");
                    }
                  }}
                />
                <button
                  className="secondary"
                  disabled={busy || !freeText.trim()}
                  onClick={() => {
                    void logObject(null, freeText.trim());
                    setFreeText("");
                  }}
                >
                  Add
                </button>
              </div>

              {session.observations.length > 0 && (
                <>
                  <h4>Logged this session</h4>
                  <table className="targets">
                    <tbody>
                      {session.observations.map((observation) => (
                        <tr key={observation.id}>
                          <td>{formatTime(observation.observed_at_utc, timeZone)}</td>
                          <td>
                            <strong>{observation.object_name}</strong>
                            {observation.notes && (
                              <div className="target-notes">{observation.notes}</div>
                            )}
                          </td>
                          <td>
                            {observation.rating
                              ? "★".repeat(observation.rating)
                              : ""}
                          </td>
                          <td>
                            <button
                              className="chip"
                              onClick={() => removeObservation(observation.id!)}
                              title="Remove"
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}

          {sessions.length > 0 && (
            <>
              <h4>Recent sessions</h4>
              <table className="targets">
                <tbody>
                  {sessions.map((s) => {
                    // The active session is refetched on every log, so take its
                    // count from the live object rather than the stale list.
                    const live = s.id === session?.id ? session : s;
                    return (
                      <tr key={s.id} className={s.id === session?.id ? "charted" : undefined}>
                        <td>{live.date}</td>
                        <td className="muted">{live.location_key}</td>
                        <td>{live.observations.length} logged</td>
                        <td className="muted">{live.notes ?? ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  );
}
