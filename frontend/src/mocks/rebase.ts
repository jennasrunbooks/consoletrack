// Moves a recorded capture's timestamps so it reads as recent: every time shifts by the same whole
// number of seconds, so durations and order stay exactly as recorded, and each keeps its format.

const ISO = /(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?(Z|\+00:00)/g; // 2026-10-21T00:53:50.589Z
const ASCTIME = /(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3})/g; // Python logging: 2026-10-21 00:53:38,357
const EPOCH = /\b(1\d{9})\.(\d{3})\b/g; // Prometheus sample times: 1792544021.382

/** "YYYY-MM-DD" and "HH:MM:SS" of a UTC time `deltaS` seconds after the given one. */
function shifted(day: string, time: string, deltaS: number): [string, string] {
  const iso = new Date(Date.parse(`${day}T${time}Z`) + deltaS * 1000).toISOString();
  return [iso.slice(0, 10), iso.slice(11, 19)];
}

/** A function that rewrites every timestamp in a string, moving `recordedAt` to `targetMs`. */
export function rebaser(recordedAt: string, targetMs: number): (text: string) => string {
  // Rounded down, so the recorded moment never lands after the target ("9 min ago", not "8").
  const deltaS = Math.floor((targetMs - Date.parse(recordedAt)) / 1000);
  return (text) =>
    text
      .replace(ISO, (_, day: string, time: string, fraction = "", zone: string) => {
        const [d, t] = shifted(day, time, deltaS);
        return `${d}T${t}${fraction}${zone}`;
      })
      .replace(ASCTIME, (_, day: string, time: string, ms: string) => {
        const [d, t] = shifted(day, time, deltaS);
        return `${d} ${t},${ms}`;
      })
      .replace(EPOCH, (_, seconds: string, ms: string) => `${Number(seconds) + deltaS}.${ms}`);
}
