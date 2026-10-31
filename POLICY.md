MT5 GUARDRAIL - INSTALLATION POLICY AND RISK ACKNOWLEDGEMENT
Version 2026-10-31

Read this before you install. The installer will not continue until you accept it.

1. RELATIONSHIP TO THE LICENSE
This policy supplements the Apache License, Version 2.0 (see the LICENSE file). It does not
remove or restrict any right the Apache License gives you. If the two conflict, the Apache
License controls. It applies to running the installer and to installing and using this software.

2. NO WARRANTY - NO LIABILITY
The software is provided "AS IS", without warranties or conditions of any kind (Apache License
sections 7 and 8 apply in full). To the maximum extent permitted by law, the authors and
contributors are not liable for any loss or damage of any kind arising from the software or from
this installer, including trading losses, lost profits, missed, wrong, duplicate or rejected
orders, account restrictions or closure by a broker, data loss, downtime, or security incidents.
Nothing in this policy excludes or limits liability for fraud, intentional misconduct, gross
negligence, or death or personal injury, or any other liability that cannot be excluded or limited
by law, and nothing limits consumer rights that the law of your country does not allow to be waived.

3. TRADING RISK - NOT ADVICE
This software is infrastructure. It is not a trading strategy, not financial, investment, legal
or tax advice, and no promise of profit. Trading leveraged products (CFDs, forex, crypto and
others) can lose more than you deposit. The guardrails (preview and confirm token, order and lot
ceilings, demo-only default, mandatory reasoning record, cooldowns) reduce specific known
failure modes. They do not remove trading risk and they do not check whether a strategy, script
or AI agent that calls the bridge is any good. Use a DEMO account first. Do not turn off the
demo-only protection until you have tested your own setup and accept all consequences.

4. YOUR RESPONSIBILITY
You are solely responsible for: every order sent through this software; the accounts, passwords
and API keys you connect; any script, bot or AI agent you let call the bridge; your broker's
terms (many brokers restrict or prohibit automated, EA or AI-assisted trading, or contests and
promotions that involve it); and the laws, licensing, reporting and tax rules that apply to you.
You confirm you are legally allowed to use this software where you live and are of legal age.

5. WHAT THE INSTALLER DOES
It runs as Administrator. It downloads this project from github.com and Python packages from
PyPI through pip, and it can run package build code if you allow source builds. It creates
files in the install folder (default C:\mt5-guardrail), creates a virtual environment, creates
a .env file containing a newly generated API key, and sets permissions so that only
Administrators and SYSTEM can change the installed files. It does not change firewall rules,
PATH, the registry or the PowerShell execution policy, does not install services, and does not
collect, send or store your MT5 login or password. The installer sends no telemetry. Third-party
packages come under their own licenses and carry their own supply-chain risk, which you accept.

6. SECURITY
Anyone who holds your API key and can reach the bridge can place trades on the connected
account. Keep .env private, never commit or share it, do not expose the bridge to untrusted
networks, and read SECURITY.md before changing the listening address. Keeping your computer,
broker account and credentials secure is your responsibility.

7. THIRD PARTIES
MetaTrader is a trademark of MetaQuotes Ltd. This project is not affiliated with or endorsed by
MetaQuotes, any broker, or any other third party. Their software and terms apply to you.

8. SEVERABILITY
If a part of this policy is not enforceable where you live, the rest still applies.

9. RECORD OF ACCEPTANCE
When you accept, the installer writes a local file (.policy-accepted.txt in the install folder)
with the policy version, a hash of this text, the UTC time, your Windows account name and how
you accepted. Nothing is transmitted anywhere.

10. VERSIONS AND TRANSLATIONS
This English text is the binding version; translations (such as POLICY.th.md) are for convenience
only. A new policy version applies to installations and updates for which you accept it; a version
you already accepted keeps governing what you installed under it.

11. IF YOU DO NOT AGREE
Do not run the installer. At the prompt answer N, or just press Enter, or press Ctrl+C: nothing is
installed.
Declining this policy does not affect the rights the Apache License gives you.
