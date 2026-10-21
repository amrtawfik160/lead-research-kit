import type { Metadata } from "next";
import { QualityPage } from "@/components/pages/quality-page";

export const metadata: Metadata = { title: "Quality" };

export default function Page(): React.ReactElement {
  return <QualityPage />;
}
