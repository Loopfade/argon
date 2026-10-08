"""Execute the dashboard's actual JS against retry and legacy data."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js required for dashboard rendering")
class DashboardRenderingTests(unittest.TestCase):
    def test_retry_history_totals_and_disjoint_timeline_bars(self):
        script = (ROOT / "index.html").read_text().split("<script>", 1)[1].split("</script>", 1)[0]
        # Replace only bootstrap side effects, leaving all rendering code intact.
        start = script.index('  $("refresh").addEventListener')
        script = script[:start] + '''
          const elements={};
          document.getElementById=id => elements[id] ||= {};
          const first={id:1,run_attempt:1,status:"completed",conclusion:"failure",
            started_at:"2026-10-05T01:00:00Z",completed_at:"2026-10-05T07:00:00Z",
            steps:[{name:"Warm local compiler cache (checkpoint 1/20)",status:"completed",conclusion:"success"}]};
          const second={...first,id:2,run_attempt:2,conclusion:"success",
            started_at:"2026-10-05T10:00:00Z",completed_at:"2026-10-05T13:00:00Z"};
          const job={...second,name:"Build and verify arm64 / build",duration_ms:9*3600000,
            started_at:first.started_at,attempts:[first,second]};
          const run={status:"completed",first_started_at:first.started_at,
            run_started_at:second.started_at,completed_at:second.completed_at,duration_ms:12*3600000};
          renderTimeline(run,[job]);
          process.stdout.write(JSON.stringify({runDuration:durationMs(run),jobDuration:durationMs(job),
            legacyDuration:durationMs(second),warm:checkpointStats(job),
            architecture:renderArchitecture("arm64-v8a",job),timeline:elements.timeline.innerHTML}));
        })();
        '''
        result = subprocess.run(["node", "-e", "global.document={getElementById:()=>({})};\n" + script],
                                capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["runDuration"], 12 * 3600_000)
        self.assertEqual(data["jobDuration"], 9 * 3600_000)
        self.assertEqual(data["legacyDuration"], 3 * 3600_000)
        self.assertEqual(data["warm"]["success"], 2)
        self.assertIn("Attempt 1 · FAILED", data["architecture"])
        self.assertIn("Attempt 2 · SUCCESS", data["architecture"])
        self.assertEqual(data["timeline"].count('class="bar '), 2)
        self.assertIn('left:75.00%', data["timeline"])
