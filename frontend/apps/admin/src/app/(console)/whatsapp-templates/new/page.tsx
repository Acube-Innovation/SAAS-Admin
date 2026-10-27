import { Suspense } from "react";

import { NewWhatsAppTemplate } from "@/components/whatsapp/wa-new";

export default function Page() {
  return (
    <Suspense>
      <NewWhatsAppTemplate />
    </Suspense>
  );
}
