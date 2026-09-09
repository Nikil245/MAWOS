# MAWOS React frontend

The frontend is a Vite React application using React Router, Tailwind CSS,
Lucide, Recharts, and a centralized relative `/api` client. The Vite
development server runs at `http://127.0.0.1:5173` and proxies `/api` to
FastAPI at `http://127.0.0.1:8000`.

```bash
cd frontend
npm install
npm run dev
```

`npm run build` produces a local deployment artifact in `dist/`; it is not
served by FastAPI and is not committed. Start the React website with Vite.

## Deliberately unavailable presentation areas

`src/data/mockAdapter.js` contains the only presentation placeholders. The
current FastAPI contract has no endpoints for library records, event check-in,
user/permission administration, backup/redeploy controls, or infrastructure
metrics. The UI labels these as unavailable instead of sending invented API
requests or simulating state-changing operations.

## Academic timetables

Role-gated timetable pages live under `src/pages/timetable/`. HODs configure,
generate and review drafts, lock lesson blocks and explicitly publish. Students
and faculty read their own published weekly/today/current-next schedules; faculty
can configure availability before publication. Admin manages terms, periods and
rooms, and principal sees read-only coverage. Scheduling and validation are
server-authoritative. See [the timetable contract](../docs/TIMETABLE.md).
