Why another deployment tool?

Deployment-tools is intended to connect three things that tend to become separate maintenance problems: reviewing changes made directly on a computer, keeping application-owned settings and data separate from that computer’s configuration, and recovering interrupted work without a central manager. That combination—not remote access or automated installation—is its proposed contribution.

[Ansible](https://docs.ansible.com/projects/ansible/latest/getting_started/basic_concepts.html)⁠ and [pyinfra](https://pyinfra.com/)⁠ are the closest starting points for automating configuration. Neither requires an always-running central service, and Ansible already supports background jobs. Using them would be reasonable, but we would still need to build the application-specific rules for preserving live changes, recording accepted operations, and recovering their effects. Deployment-tools aims to make those rules a shared system rather than another collection of local scripts. 

[Kamal](https://kamal-deploy.org/)⁠ is close to the preferred direct-management workflow. Its focus is deploying web applications and returning to earlier software versions. Our problem also includes the computer’s configuration and the changing settings and data inside applications. Choosing Kamal would leave that broader maintenance and recovery model to separate tools. 

[Komodo](https://komo.do/docs/intro)⁠ and [Coolify](https://coolify.io/docs/core/how-coolify-works)⁠ already bring deployment, monitoring, and multiple-machine management together. Their management services organize configuration and coordinate actions; this does not mean running applications stop when those services disappear. We want to go further in separating management from execution: each machine should directly authorize, remember, and recover its accepted work without reconstructing a central management installation. 

[YunoHost](https://doc.yunohost.org/en/admin/what_is_yunohost/)⁠ already combines application management, user accounts, and backups. It does so within a managed server environment based on Debian. We want to preserve independently configured computers and application definitions rather than adopt that complete environment. 

[HomeButler](https://github.com/Higangssh/homebutler)⁠ is particularly close to the original problem: it documents on-demand remote use, change reports, application installation, and restore drills. Those features therefore are not novel claims for deployment-tools. The additional design we want is local programs responsible for accepting and recovering changes—not only tools for observing systems and invoking maintenance. 

These alternatives can be extended or combined. The reason to build deployment-tools is to make the agreement between configuration, application data, and recoverable local work reusable and testable—not to claim that the individual capabilities are new.

That remains an intended distinction, not a demonstrated reliability advantage: deployment-tools is currently a reference scaffold, without production-qualified native management.