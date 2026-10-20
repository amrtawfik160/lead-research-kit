import { ContactsPage } from "@/components/pages/contacts-page";
import { loadApi, type ContactsPayload } from "@/lib/load";

export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <ContactsPage initial={await loadApi<ContactsPayload>("/api/contacts")} />;
}
