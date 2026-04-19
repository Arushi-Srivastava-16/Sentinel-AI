import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useWebSocket } from "@/hooks/useWebSocket";
import { StatusBar } from "@/components/StatusBar";
import { MetricsCards } from "@/components/MetricsCards";
import { EventFeed } from "@/components/EventFeed";
import { VerdictChart } from "@/components/VerdictChart";
import { GraphViewer } from "@/components/GraphViewer";
import { PolicyPanel } from "@/components/PolicyPanel";

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 } },
});

function Dashboard() {
  useWebSocket();  // connects & keeps alive

  return (
    <div className="min-h-screen bg-sentinel-bg text-white">
      <StatusBar />

      <main className="max-w-[1600px] mx-auto px-4 py-6 space-y-6">
        {/* Row 1: KPI tiles */}
        <MetricsCards />

        {/* Row 2: Event feed + Charts */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 h-[420px]">
            <EventFeed />
          </div>
          <div className="space-y-4">
            <VerdictChart />
          </div>
        </div>

        {/* Row 3: Graph + Policy */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <GraphViewer />
          <PolicyPanel />
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Dashboard />
    </QueryClientProvider>
  );
}
