import { JobsPage } from "@/components/pages/jobs-page";
import { loadApi, type JobsPayload } from "@/lib/load";

export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <JobsPage initial={await loadApi<JobsPayload>("/api/jobs")} />;
}
