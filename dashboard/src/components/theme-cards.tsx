import { CircleCheck, Moon, Sun } from "lucide-react";
import { useTheme } from "@/components/theme-provider";
import { cn } from "@/lib/utils";

const LIGHT = { bg: "#FBFAF8", s: "#F2F1EE", t: "#1D1C1A", b: "#E3E1DC", a: "#4B5BD6" };
const DARK = { bg: "#1D1D1C", s: "#151514", t: "#ECEAE5", b: "#333230", a: "#8E9AF3" };

function Preview({ c }: { c: typeof LIGHT }): React.ReactElement {
  return (
    <>
      <div className="flex flex-col gap-1 px-1.5 py-2" style={{ background: c.s }}>
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.b, width: "70%" }} />
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.a, width: "55%" }} />
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.b, width: "80%" }} />
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.b, width: "60%" }} />
      </div>
      <div className="flex flex-col gap-1.5 p-2.5" style={{ background: c.bg }}>
        <i className="block h-[7px] rounded-[3px]" style={{ background: c.t, width: "45%" }} />
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.b }} />
        <i className="block h-[5px] rounded-[3px]" style={{ background: c.b, width: "80%" }} />
        <i className="mt-1 block h-2.5 rounded-[3px]" style={{ background: c.a, width: "30%" }} />
      </div>
    </>
  );
}

export function ThemeCards(): React.ReactElement {
  const { theme, setTheme } = useTheme();
  return (
    <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label="Theme">
      {(
        [
          ["light", "Light", Sun, LIGHT],
          ["dark", "Dark", Moon, DARK],
        ] as const
      ).map(([value, label, Icon, colors]) => {
        const on = theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => setTheme(value)}
            className={cn(
              "flex flex-col gap-2 rounded-[10px] border p-2 text-left transition-colors",
              on ? "border-primary shadow-[0_0_0_3px_color-mix(in_srgb,var(--primary)_18%,transparent)]" : "hover:border-[#dad7d0] dark:hover:border-[#3a3936]",
            )}
          >
            <div className="grid h-[84px] grid-cols-[30%_1fr] overflow-hidden rounded-[6px] border border-black/10">
              <Preview c={colors} />
            </div>
            <span className="flex items-center gap-1.5 text-[13px] font-medium">
              <Icon className="size-3.5" />
              {label}
              {on ? <CircleCheck className="ms-auto size-4 text-primary" /> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}
