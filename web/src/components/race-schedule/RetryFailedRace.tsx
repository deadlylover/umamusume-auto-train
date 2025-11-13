import { Checkbox } from "../ui/checkbox";

type Props = {
  retryFailedRace: boolean;
  setRetryFailedRace: (newState: boolean) => void;
};

export default function RetryFailedRace({
  retryFailedRace,
  setRetryFailedRace,
}: Props) {
  return (
    <div className="w-fit">
      <label htmlFor="retry-failed-race" className="flex flex-col gap-1">
        <span className="flex items-center gap-2">
          <Checkbox
            id="retry-failed-race"
            checked={retryFailedRace}
            onCheckedChange={() => setRetryFailedRace(!retryFailedRace)}
          />
          <span className="text-lg font-medium shrink-0">
            Retry Failed Race?
          </span>
        </span>
        <span className="text-sm text-muted-foreground">
          Use Try Again instead of cancelling when the Next button is missing
          after a loss.
        </span>
      </label>
    </div>
  );
}
