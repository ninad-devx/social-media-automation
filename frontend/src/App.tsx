import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { QuickPublishPage } from "./pages/QuickPublishPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PostsPage } from "./pages/PostsPage";
import { CalendarPage } from "./pages/CalendarPage";
import { AccountsPage } from "./pages/AccountsPage";
import { SettingsLayout } from "./pages/SettingsLayout";
import { SettingsOverview } from "./components/settings/SettingsOverview";
import { BufferIntegrationsPage } from "./components/settings/BufferIntegrationsPage";
import { AccountsChannelsPage } from "./components/settings/AccountsChannelsPage";
import { AiSettingsPage } from "./pages/AiSettingsPage";
import { AiAutomationPage } from "./pages/AiAutomationPage";
import { CampaignHistoryPage, CampaignDetailPage } from "./pages/CampaignHistoryPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<QuickPublishPage />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="posts" element={<PostsPage />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="ai" element={<AiAutomationPage />} />
          <Route path="campaigns" element={<CampaignHistoryPage />} />
          <Route path="campaigns/:campaignId" element={<CampaignDetailPage />} />
          <Route path="accounts" element={<AccountsPage />} />

          {/* Settings is a tabbed layout; each tab is its own route. */}
          <Route path="settings" element={<SettingsLayout />}>
            <Route index element={<SettingsOverview />} />
            <Route path="integrations" element={<BufferIntegrationsPage />} />
            <Route path="accounts" element={<AccountsChannelsPage />} />
          </Route>

          {/* AI settings keeps its own page and its own URL. */}
          <Route path="settings/ai" element={<AiSettingsPage />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}