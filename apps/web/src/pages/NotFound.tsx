import { useLocation } from "react-router-dom";
import { ButtonLink, EmptyState } from "../design";
import { Instrument } from "../design/Instrument";

export function NotFound() {
  const { pathname } = useLocation();
  return (
    <div className="page flex h-full items-end pb-16">
      <Instrument>
        <EmptyState
          size="stage"
          title="No such instrument"
          reason={`Nothing is mounted at ${pathname}.`}
          action={<ButtonLink to="/">Back to Command Center</ButtonLink>}
        />
      </Instrument>
    </div>
  );
}
