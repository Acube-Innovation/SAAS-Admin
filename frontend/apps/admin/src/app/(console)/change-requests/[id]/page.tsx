import { Suspense } from "react";

import { ChangeRequestDetail } from "@/components/change-requests/detail";

export default async function ChangeRequestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <ChangeRequestDetail id={id} />
    </Suspense>
  );
}
