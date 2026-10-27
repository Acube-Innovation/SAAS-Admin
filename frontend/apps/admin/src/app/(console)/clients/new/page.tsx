import type { Metadata } from "next";

import { CreateClientForm } from "@/components/client/create-client-form";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("client_form.title") };

export default function NewClientPage() {
  return <CreateClientForm />;
}
